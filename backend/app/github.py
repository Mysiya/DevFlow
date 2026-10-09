import asyncio
import copy
import json
import logging
from urllib.parse import quote

import httpx

from .config import Settings

logger=logging.getLogger(__name__)


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    """Read-only API adapter. No POST/PATCH/DELETE capability is exposed."""

    def __init__(self, settings: Settings, cache: dict | None = None):
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "DevFlow-AI"}
        if settings.github_token.get_secret_value():
            headers["Authorization"] = f"Bearer {settings.github_token.get_secret_value()}"
        self.client = httpx.AsyncClient(base_url="https://api.github.com", headers=headers, timeout=30)
        self.cache = copy.deepcopy(cache or {})
        self.cache_hits = 0

    async def close(self):
        await self.client.aclose()

    async def get(self, path: str, params: dict | None = None):
        try:
            response = await self.client.get(path, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise GitHubError(f"GitHub 请求失败（HTTP {exc.response.status_code}），请检查仓库名称、Token 权限或 API 限额。") from exc
        except httpx.HTTPError as exc:
            logger.warning("GitHub transport failed: %s / %s",type(exc).__name__,type(exc.__cause__).__name__)
            raise GitHubError("GitHub 网络连接失败，请稍后重试。") from exc

    async def cached_get(self, path, params=None, optional=False):
        key = path + ":" + json.dumps(params or {}, sort_keys=True)
        cached = self.cache.get(key)
        headers = {"If-None-Match": cached["etag"]} if cached else {}
        try:
            response = await self.client.get(path, params=params, headers=headers)
            if response.status_code == 304 and cached:
                self.cache_hits += 1
                return cached["data"]
            if optional and response.status_code in (404, 409):
                self.cache.pop(key, None)
                return None
            response.raise_for_status()
            data = response.json()
            if response.headers.get("etag"):
                self.cache[key] = {"etag": response.headers["etag"], "data": data}
            else:
                self.cache.pop(key, None)
            return data
        except httpx.HTTPStatusError as exc:
            raise GitHubError(f"GitHub 同步失败（HTTP {exc.response.status_code}），请检查仓库与 API 限额。") from exc
        except httpx.HTTPError as exc:
            logger.warning("GitHub sync transport failed: %s / %s",type(exc).__name__,type(exc.__cause__).__name__)
            raise GitHubError("GitHub 网络连接失败，请稍后重试。") from exc

    async def snapshot(self, repo: str, previous=None, scopes=None):
        allowed={"repository","issues","pulls","runs","documents"}
        selected=set(scopes or allowed)
        if not selected<=allowed:raise GitHubError("同步范围无效。")
        if previous is None:selected=allowed
        snapshot=copy.deepcopy(previous or {})
        # Even partial refreshes revalidate identity: a deleted repository name
        # can be reused by a different GitHub repository while an event is queued.
        meta=await self.cached_get(f"/repos/{repo}")
        if meta["full_name"].lower()!=repo.lower():raise GitHubError("仓库名称已变化，请核对后重新连接。")
        old_id=snapshot.get("github_repository_id")
        if old_id is not None and old_id!=meta.get("id"):raise GitHubError("远程仓库 ID 已变化，已停止同步以保留原快照。")
        if "repository" in selected or "documents" in selected:
            selected.update(("repository","documents"))
            branch=await self.cached_get(f"/repos/{repo}/branches/{quote(meta['default_branch'],safe='')}",optional=True)
            if branch is None and meta.get("size",0)>0:raise GitHubError("未能读取非空仓库的默认分支，已停止同步以保留已有文档。")
            snapshot.update(name=meta["full_name"],description=meta.get("description") or "GitHub 仓库",branch=meta["default_branch"],head_sha=branch["commit"]["sha"] if branch else None,github_repository_id=meta.get("id"))
        async def section(name):
            if name=="issues":
                rows=await self.cached_get(f"/repos/{repo}/issues",{"state":"open","per_page":100,"sort":"updated"})
                return [{"number":x["number"],"title":x["title"],"body":x.get("body") or "","state":x["state"],"labels":[y["name"] for y in x["labels"]],"url":x["html_url"],"updated_at":x["updated_at"]} for x in rows if "pull_request" not in x]
            if name=="pulls":
                rows=await self.cached_get(f"/repos/{repo}/pulls",{"state":"open","per_page":100})
                return [{"number":x["number"],"title":x["title"],"body":x.get("body") or "","state":x["state"],"draft":x["draft"],"head_sha":x["head"]["sha"],"base_sha":x["base"]["sha"],"url":x["html_url"]} for x in rows]
            if name=="runs":return [self.run_info(x) for x in (await self.cached_get(f"/repos/{repo}/actions/runs",{"per_page":30}))["workflow_runs"]]
            readme=await self.readme(repo,snapshot["head_sha"]) if snapshot.get("head_sha") else None
            return [readme] if readme else []
        names=sorted(selected-{"repository"})
        snapshot.update(zip(names,await asyncio.gather(*(section(name) for name in names))))
        snapshot.update(source="github",coverage="同步最近更新的最多 100 条开放 Issue（GitHub Issues 分页包含 PR）、100 个开放 PR、30 次 Actions；属于部分范围的快照。",sync_info={"etag_cache_hits":self.cache_hits,"empty_repository":snapshot.get("head_sha") is None,"refreshed_sections":sorted(selected),"strategy":"full" if selected==allowed else "event-scoped"})
        revision=snapshot.get("head_sha")
        snapshot["_http_cache"]={key:value for key,value in self.cache.items() if "/readme:" not in key or revision and revision in key}
        return snapshot

    async def readme(self, repo: str, revision: str):
        import base64

        data = await self.cached_get(f"/repos/{repo}/readme", {"ref": revision}, optional=True)
        if data is None:
            return None
        path = data.get("path") or "README.md"
        return {"id": "doc:readme", "title": "README", "path": path, "revision": revision, "content": base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")[:60000], "url": f"https://github.com/{repo}/blob/{revision}/{quote(path, safe='/')}"}

    @staticmethod
    def run_info(x: dict):
        return {"id": x["id"], "name": x.get("name") or "Workflow", "status": x["status"], "conclusion": x.get("conclusion"), "head_sha": x["head_sha"], "url": x["html_url"], "created_at": x["created_at"]}

    async def issue(self, repo: str, number: int):
        data = await self.get(f"/repos/{repo}/issues/{number}")
        if "pull_request" in data:
            raise ValueError("目标是 PR，请使用 PR 分析。")
        return {"number": number, "title": data["title"], "body": data.get("body") or "", "labels": [x["name"] for x in data["labels"]], "state": data["state"], "url": data["html_url"]}

    async def pr(self, repo: str, number: int):
        data, files = await asyncio.gather(self.get(f"/repos/{repo}/pulls/{number}"), self.get(f"/repos/{repo}/pulls/{number}/files", {"per_page": 100}))
        checks = await self.get(f"/repos/{repo}/actions/runs", {"head_sha": data["head"]["sha"], "per_page": 30})
        return {"number": number, "title": data["title"], "body": data.get("body") or "", "head_sha": data["head"]["sha"], "base_sha": data["base"]["sha"], "url": data["html_url"], "files": [{k: x.get(k) for k in ("filename", "status", "additions", "deletions", "patch")} for x in files], "checks": [self.run_info(x) for x in checks["workflow_runs"]], "coverage": "最多 100 个变更文件、30 次该 head SHA 的 Actions；不含外部 CI、分支保护和全部 Review，不能据此保证可合入。"}

    async def ci(self, repo: str, run_id: int):
        data, jobs = await asyncio.gather(self.get(f"/repos/{repo}/actions/runs/{run_id}"), self.get(f"/repos/{repo}/actions/runs/{run_id}/jobs", {"per_page": 100}))
        result = self.run_info(data)
        logs, gaps = [], []
        failed = [x for x in jobs["jobs"] if x.get("conclusion") in ("failure", "timed_out")]
        for job in failed[:3]:
            response = await self.client.get(f"/repos/{repo}/actions/jobs/{job['id']}/logs", follow_redirects=False)
            if response.status_code not in (301, 302, 303, 307, 308) or not response.headers.get("location", "").startswith("https://"):
                gaps.append(f"无法获取 {job['name']} 日志，可能缺少 Actions read 权限或日志已过期。")
                continue
            # Download signed logs without forwarding the GitHub Authorization header.
            async with httpx.AsyncClient(timeout=30) as download:
                async with download.stream("GET", response.headers["location"]) as log_response:
                    log_response.raise_for_status()
                    chunks, size = [], 0
                    async for chunk in log_response.aiter_bytes():
                        remaining = 512_000 - size
                        chunks.append(chunk[:remaining])
                        size += len(chunk)
                        if size >= 512_000:
                            gaps.append(f"{job['name']} 日志截断至 512 KB。")
                            break
                    logs.append(f"Job: {job['name']}\n" + b"".join(chunks).decode("utf-8", errors="replace"))
        if len(failed) > 3:
            gaps.append("只读取前三个失败 job 的日志。")
        result.update(logs="\n".join(logs)[:25000], log_gaps=gaps, jobs=[{"name": x["name"], "conclusion": x.get("conclusion")} for x in jobs["jobs"]])
        return result
