"""Read immutable Git objects. No checkout, hook, build, or repository script execution."""
import asyncio
import base64
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

from .db import CodeWorkspace, SessionLocal, utcnow
from .github import GitHubClient
from .knowledge import digest
from .code_search import ranked_search, VERSION


class WorkspaceError(RuntimeError):
    pass


EXCLUDED = {".git", ".codex", ".agents", ".aws", ".ssh", ".venv", ".eval-venv", ".vector-venv", ".local", ".gradle", "node_modules", ".next", "__pycache__", ".pytest_cache", "data", "artifacts", "volumes", "dist", "build", "coverage"}
EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".sql", ".sh", ".ps1", ".html", ".css", ".go", ".java", ".rs", ".c", ".h", ".cpp", ".rb", ".php", ".swift", ".kt", ".txt"}
LOCKS: dict[str, asyncio.Lock] = {}


def safe_path(path):
    if not isinstance(path, str) or not path or len(path) > 500 or "\\" in path or ":" in path or any(ord(x) < 32 or ord(x) == 127 for x in path):
        raise WorkspaceError("代码路径必须是仓库内的相对路径。")
    if any(x in ("", ".", "..") for x in path.split("/")):
        raise WorkspaceError("代码路径不能越出当前仓库。")
    return path


def readable_path(path):
    try:
        safe_path(path)
    except WorkspaceError:
        return False
    parts = path.split("/")
    if any(x.lower() in EXCLUDED for x in parts[:-1]):
        return False
    name = parts[-1].lower()
    if name.startswith(".env") or name.endswith(".env"):
        return name.endswith(".example")
    if name.startswith(".") and name not in (".gitignore", ".dockerignore"):
        return False
    if any(x in name for x in ("credential", "secret", "private_key", "id_rsa", "id_ed25519")) or name.endswith((".pem", ".key", ".p12", ".pfx", ".keystore")):
        return False
    return Path(name).suffix in EXTENSIONS or name in ("dockerfile", "makefile", "readme", "license", "notice", ".gitignore", ".dockerignore")


def redact(text, settings):
    original = text
    for field in ("github_token", "llm_api_key", "embedding_api_key", "rerank_api_key", "milvus_token", "bootstrap_admin_password", "mcp_access_token", "github_webhook_secret"):
        value = getattr(settings, field).get_secret_value()
        if len(value) > 8:
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b", "[REDACTED]", text)
    return text, text != original


def git(args, directory=None, data=None, token="", limit=12000000):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1")
    # Scoped credentials are passed only in process environment, never in argv/config/URL.
    if token:
        auth = base64.b64encode(("x-access-token:" + token).encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraHeader", GIT_CONFIG_VALUE_0="Authorization: Basic " + auth)
    command = ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "credential.helper=", "-c", "protocol.file.allow=never", "-c", "protocol.ext.allow=never"]
    if directory:
        command += ["--git-dir=" + str(directory)]
    try:
        proc = subprocess.run(command + args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=120, check=False)
    except FileNotFoundError as exc:
        raise WorkspaceError("未找到 Git，请安装 Git 并重启后端。") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkspaceError("Git 操作失败或超时，请检查仓库和网络。") from exc
    if proc.returncode:
        raise WorkspaceError("Git 操作失败，请检查提交是否存在、仓库读取权限和网络。")
    if len(proc.stdout) > limit:
        raise WorkspaceError("Git 返回内容超过工作区读取上限。")
    return proc.stdout


class WorkspaceManager:
    def __init__(self, settings):
        self.settings = settings
        self.root = Path(settings.workspace_root).resolve()
        candidate = Path(__file__).resolve().parents[2]
        self.project = candidate if (candidate / "frontend" / "package.json").is_file() and (candidate / "backend" / "app").is_dir() else None

    def directory(self, repo_id, source):
        if not re.fullmatch(r"[0-9a-f]{32}", repo_id) or source not in ("github", "local_project"):
            raise WorkspaceError("工作区范围无效。")
        return self.root / repo_id / (source + ".git")

    def ref(self, repo_id):
        with SessionLocal() as db:
            state = db.get(CodeWorkspace, repo_id)
            if state and state.status == "ready" and state.sha:
                return {"repository_id": repo_id, "source": state.source, "sha": state.sha}
        return None

    def status(self, repo_id):
        with SessionLocal() as db:
            state = db.get(CodeWorkspace, repo_id)
            return {"source": state.source if state else None, "status": state.status if state else "empty", "sha": state.sha if state else None, "file_count": state.file_count if state else 0, "message": state.message if state else "尚未同步代码", "updated_at": state.updated_at.isoformat() if state else None, "local_import_available": bool(self.project and self.settings.local_project_enabled)}

    def _init(self, directory):
        if not directory.exists():
            directory.parent.mkdir(parents=True, exist_ok=True)
            git(["-c", "init.templateDir=", "init", "--bare", str(directory)])

    def _tree(self, ref):
        sha = ref.get("sha", "")
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise WorkspaceError("代码读取需要完整的提交 SHA。")
        directory = self.directory(ref["repository_id"], ref["source"])
        raw = git(["ls-tree", "-r", "-l", "-z", sha], directory, limit=8000000)
        files, omitted = [], 0
        for record in raw.split(b"\0"):
            if not record:
                continue
            header, name = record.split(b"\t", 1)
            mode, kind, blob, size = header.split()
            try:
                path = name.decode("utf-8")
            except UnicodeError:
                omitted += 1; continue
            if kind != b"blob" or mode not in (b"100644", b"100755") or not readable_path(path) or int(size) > self.settings.workspace_max_file_bytes or len(files) >= self.settings.workspace_max_files:
                omitted += 1; continue
            files.append({"path": path, "blob": blob.decode(), "size": int(size)})
        return files, omitted

    def files(self, ref):
        if not ref:
            raise WorkspaceError("代码工作区尚未就绪，请先同步或导入本地源码。")
        files, omitted = self._tree(ref)
        return {"sha": ref["sha"], "source": ref["source"], "files": files, "omitted_files": omitted, "coverage": "仅列出可读取的文本文件；排除密钥文件、依赖目录、符号链接、子模块与超限文件。"}

    def _blobs(self, ref, files):
        if not files:
            return []
        directory = self.directory(ref["repository_id"], ref["source"])
        data = git(["cat-file", "--batch"], directory, data="".join(x["blob"] + "\n" for x in files).encode(), limit=self.settings.workspace_max_total_bytes + len(files) * 100)
        offset, results = 0, []
        for file in files:
            end = data.index(b"\n", offset)
            header = data[offset:end].split()
            if len(header) != 3 or header[0].decode() != file["blob"] or header[1] != b"blob":
                raise WorkspaceError("Git 文件对象无效。")
            size = int(header[2]); offset = end + 1
            raw = data[offset:offset + size]; offset += size + 1
            if b"\0" in raw:
                results.append(None); continue
            try:
                text = raw.decode("utf-8")
            except UnicodeError:
                results.append(None); continue
            text, redacted = redact(text, self.settings)
            # Local snapshots are redacted before storage; preserve that visible marker.
            redacted = redacted or "[REDACTED]" in text
            results.append((text, redacted))
        return results

    def read(self, ref, path, line_start=1, line_end=200):
        safe_path(path)
        if not readable_path(path):
            raise WorkspaceError("此路径不在允许读取的源码范围内。")
        if not ref:
            raise WorkspaceError("代码工作区尚未就绪。")
        if not 1 <= line_start <= line_end or line_end - line_start >= 400:
            raise WorkspaceError("一次最多读取 400 行，行号从 1 开始。")
        files, _ = self._tree(ref)
        file = next((x for x in files if x["path"] == path), None)
        if not file:
            raise WorkspaceError("该提交中没有此可读文件。")
        decoded = self._blobs(ref, [file])[0]
        if decoded is None:
            raise WorkspaceError("文件不是 UTF-8 文本，无法读取。")
        text, redacted = decoded
        lines = text.splitlines()
        if not lines and line_start == 1:
            hit = self._hit(ref, path, "", 1, 0, redacted, 0)
            hit["citation"] = path + "（空文件）"
            return hit
        if line_start > len(lines):
            raise WorkspaceError("起始行超出文件范围。")
        end = min(line_end, len(lines))
        excerpt = "\n".join(lines[line_start - 1:end])
        if len(excerpt) > 16000:
            raise WorkspaceError("代码片段过长，请缩小读取行号范围。")
        return self._hit(ref, path, excerpt, line_start, end, redacted, len(lines))

    def _hit(self, ref, path, content, start, end, redacted=False, total_lines=None):
        source, sha = ref["source"], ref["sha"]
        return {"id": f"code:{source}:{sha}:{digest(path)[:12]}:{start}:{end}", "path": path, "title": path, "content": content, "sha": sha, "source": source, "line_start": start, "line_end": end, "citation": f"{path} L{start}-L{end}", "redacted": redacted, "total_lines": total_lines, "url": ""}

    def search(self, ref, query, path_prefix="", limit=8):
        if not query.strip() or len(query) > 1000:
            raise WorkspaceError("请输入有效的代码查询。")
        if not 1 <= limit <= 20:
            raise WorkspaceError("代码检索一次最多返回 20 个片段。")
        if path_prefix:
            safe_path(path_prefix.rstrip("/"))
        files, omitted = self._tree(ref) if ref else ([], 0)
        if not ref:
            raise WorkspaceError("代码工作区尚未就绪，请先同步或导入本地源码。")
        files = [x for x in files if not path_prefix or x["path"] == path_prefix or x["path"].startswith(path_prefix.rstrip("/") + "/")]
        selected, total = [], 0
        for file in files:
            if total + file["size"] > self.settings.workspace_max_total_bytes:
                omitted += 1; continue
            total += file["size"]; selected.append(file)
        readable, unreadable = [], 0
        for file, decoded in zip(selected, self._blobs(ref, selected)):
            if decoded is None:
                unreadable += 1
                continue
            text, redacted = decoded
            readable.append({"path": file["path"], "text": text, "redacted": redacted})
        ranked = ranked_search(readable, query, limit)
        hits = []
        for chunk in ranked.pop("results"):
            hit = self._hit(ref, chunk["path"], chunk["content"], chunk["line_start"], chunk["line_end"], chunk["redacted"], chunk["total_lines"])
            hit.update({key: chunk[key] for key in ("score", "symbol", "symbol_start", "symbol_end", "partial_symbol", "chunk_kind", "matched_terms", "retrieval_method")})
            hits.append(hit)
        return {"query": query, "sha": ref["sha"], "source": ref["source"], "results": hits, "searched_files": len(selected), "omitted_files": omitted,
                "unreadable_files": unreadable, "retrieval_method": VERSION, **ranked,
                "coverage": "固定提交的 BM25 源码检索，标识符拆词并优先匹配 Python 定义；其他语言及无法解析的 Python 按行分段。得分用于排序，不是正确率；未执行代码。"}

    def _local_snapshot(self, repo_id):
        if not self.project or not self.settings.local_project_enabled:
            raise WorkspaceError("当前部署没有开放本地项目源码导入。")
        directory = self.directory(repo_id, "local_project")
        self._init(directory)
        nodes, total, count = {}, 0, 0
        for folder, dirs, names in os.walk(self.project, followlinks=False):
            dirs[:] = sorted(x for x in dirs if x.lower() not in EXCLUDED and not (Path(folder) / x).is_symlink() and (Path(folder) / x).resolve().is_relative_to(self.project))
            for name in sorted(names):
                file = Path(folder) / name
                path = file.relative_to(self.project).as_posix()
                if not readable_path(path) or file.is_symlink() or not file.resolve().is_relative_to(self.project) or file.stat().st_size > self.settings.workspace_max_file_bytes:
                    continue
                raw = file.read_bytes()
                if b"\0" in raw:
                    continue
                try:
                    text, _ = redact(raw.decode("utf-8"), self.settings)
                except UnicodeError:
                    continue
                raw = text.encode("utf-8")
                if count >= self.settings.workspace_max_files or total + len(raw) > self.settings.workspace_max_total_bytes:
                    raise WorkspaceError("本地源码超过快照上限，请调整 WORKSPACE_MAX_FILES / WORKSPACE_MAX_TOTAL_BYTES。")
                blob = git(["hash-object", "-w", "--stdin"], directory, raw).decode().strip()
                node = nodes
                for part in path.split("/")[:-1]:
                    node = node.setdefault(part, {})
                node[name] = blob; count += 1; total += len(raw)
        if not count:
            raise WorkspaceError("没有可导入的项目源码。")
        def tree(node):
            records = []
            for name, value in sorted(node.items()):
                kind, sha, mode = ("tree", tree(value), "040000") if isinstance(value, dict) else ("blob", value, "100644")
                records.append(f"{mode} {kind} {sha}\t{name}\0".encode("utf-8"))
            return git(["mktree", "-z"], directory, b"".join(records)).decode().strip()
        root_tree = tree(nodes)
        previous = self.ref(repo_id)
        if previous and previous["source"] == "local_project" and git(["rev-parse", previous["sha"] + "^{tree}"], directory).decode().strip() == root_tree:
            return previous["sha"]
        args = ["-c", "user.name=DevFlow", "-c", "user.email=local-snapshot@devflow.invalid", "commit-tree", root_tree, "-m", "Local source snapshot"]
        if previous and previous["source"] == "local_project":
            args += ["-p", previous["sha"]]
        sha = git(args, directory).decode().strip()
        git(["update-ref", "refs/heads/snapshot", sha], directory)
        return sha

    async def sync(self, repo_id, repo_name, source, snapshot):
        lock = LOCKS.setdefault(repo_id, asyncio.Lock())
        async with lock:
            if source == "local_project":
                sha = await asyncio.to_thread(self._local_snapshot, repo_id)
            elif source == "github":
                if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo_name):
                    raise WorkspaceError("GitHub 仓库名称无效。")
                client = GitHubClient(self.settings)
                try:
                    meta = await client.get(f"/repos/{repo_name}")
                    if meta.get("size", 0) > 100000:
                        raise WorkspaceError("当前代码工作区仅支持 GitHub 元数据大小不超过 100 MB 的仓库。")
                    if meta.get("size", 0) == 0 and not snapshot.get("head_sha"):
                        if self.ref(repo_id):
                            raise WorkspaceError("远程仓库还没有提交，当前已导入的代码快照继续保留。")
                        return self.status(repo_id) | {"message": "远程仓库还没有提交，可先导入本地项目源码。"}
                    branch = await client.get(f"/repos/{repo_name}/branches/{quote(meta['default_branch'], safe='')}")
                    sha = branch["commit"]["sha"]
                finally:
                    await client.close()
                if not re.fullmatch(r"[0-9a-f]{40}", sha):
                    raise WorkspaceError("GitHub 未返回有效提交。")
                directory = self.directory(repo_id, source)
                await asyncio.to_thread(self._init, directory)
                await asyncio.to_thread(git, ["fetch", "--depth=1", "--no-tags", f"https://github.com/{repo_name}.git", sha], directory, token=self.settings.github_token.get_secret_value())
                await asyncio.to_thread(git, ["update-ref", "refs/heads/snapshot", sha], directory)
            else:
                raise WorkspaceError("不支持的代码来源。")
            files, omitted = await asyncio.to_thread(self._tree, {"repository_id": repo_id, "source": source, "sha": sha})
            with SessionLocal() as db:
                state = db.get(CodeWorkspace, repo_id)
                if not state:
                    state = CodeWorkspace(repository_id=repo_id, source=source); db.add(state)
                state.source, state.sha, state.status, state.file_count = source, sha, "ready", len(files)
                state.message = ("GitHub 代码已同步" if source == "github" else "本地项目源码已导入，尚未推送到 GitHub") + f"；{len(files)} 个可读文件，{omitted} 个文件未纳入读取范围。"
                state.updated_at = utcnow(); db.commit()
            return self.status(repo_id)
