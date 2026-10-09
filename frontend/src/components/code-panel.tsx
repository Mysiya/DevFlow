"use client";
import { useEffect, useState } from "react";
import {
  Code2,
  GitBranch,
  FileText,
  Search,
  RefreshCw,
  LoaderCircle,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  ExternalLink,
} from "lucide-react";
import { api, CodeFiles, CodeHit, CodeSearch, CodeStatus } from "@/lib/api";

export function CodePanel({
  repositoryId,
  emptyRemote,
  busy,
  onAsk,
}: {
  repositoryId: string;
  emptyRemote: boolean;
  busy: boolean;
  onAsk: (question: string) => void;
}) {
  const root = `/repositories/${repositoryId}/code`;
  const [status, setStatus] = useState<CodeStatus | null>(null);
  const [tree, setTree] = useState<CodeFiles | null>(null);
  const [source, setSource] = useState("github");
  const [query, setQuery] = useState("");
  const [prefix, setPrefix] = useState("");
  const [filter, setFilter] = useState("");
  const [searchResult, setSearchResult] = useState<CodeSearch | null>(null);
  const [file, setFile] = useState<CodeHit | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    api<CodeStatus>(`${root}/status`)
      .then(async (next) => {
        if (!alive) return;
        setStatus(next);
        setSource(
          next.source ||
            (emptyRemote && next.local_import_available
              ? "local_project"
              : "github"),
        );
        if (next.status === "ready") {
          const files = await api<CodeFiles>(`${root}/files`);
          if (alive) setTree(files);
        }
      })
      .catch((e) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [root, emptyRemote]);
  async function sync() {
    setPending(true);
    setError("");
    try {
      const next = await api<CodeStatus>(`${root}/sync`, {
        method: "POST",
        body: JSON.stringify({ source }),
      });
      setStatus(next);
      if (next.status === "ready") {
        setTree(await api<CodeFiles>(`${root}/files`));
        setFile(null);
        setSearchResult(null);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }
  async function search() {
    setPending(true);
    setError("");
    setSearchResult(null);
    try {
      setSearchResult(
        await api<CodeSearch>(`${root}/search`, {
          method: "POST",
          body: JSON.stringify({ query, path_prefix: prefix }),
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }
  async function read(
    path: string,
    start = 1,
    ref: { sha?: string; source?: string } | null = tree,
  ) {
    setPending(true);
    setError("");
    const params = new URLSearchParams({
      path,
      line_start: String(start),
      line_end: String(start + 199),
    });
    if (ref?.sha) params.set("revision", ref.sha);
    if (ref?.source) params.set("source", ref.source);
    try {
      setFile(await api<CodeHit>(`${root}/file?${params}`));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }
  return (
    <div className="code-page">
      <section className="panel code-workspace-summary">
        <span className="document-icon">
          <GitBranch size={23} />
        </span>
        <div>
          <h3>
            {status?.status === "ready"
              ? `${status.file_count} 个可读文件`
              : "准备代码工作区"}
          </h3>
          <p>{status?.message || "正在读取工作区状态…"}</p>
          {status?.sha && (
            <code>
              {status.source === "local_project"
                ? "本地项目快照"
                : "GitHub 提交"}{" "}
              · {status.sha.slice(0, 12)}
            </code>
          )}
        </div>
        <div className="code-sync-actions">
          <select
            aria-label="代码来源"
            value={source}
            onChange={(e) => setSource(e.target.value)}
            disabled={pending}
          >
            <option value="github">GitHub 默认分支</option>
            <option
              value="local_project"
              disabled={!status?.local_import_available}
            >
              本地项目源码
            </option>
          </select>
          <button
            className="button primary"
            onClick={sync}
            disabled={pending || !status}
          >
            {pending ? (
              <LoaderCircle className="spin" size={15} />
            ) : (
              <RefreshCw size={15} />
            )}{" "}
            {source === "local_project" ? "创建源码快照" : "同步远程代码"}
          </button>
        </div>
      </section>
      {error && (
        <div className="knowledge-message error" role="alert">
          {error}
        </div>
      )}
      {status?.source === "local_project" && (
        <div className="info-banner">
          <Code2 size={17} />
          本地快照固定于上述提交，不代表已发布到
          GitHub。创建新快照后，已保存分析仍引用旧提交。
        </div>
      )}
      {emptyRemote && !status?.sha && (
        <div className="info-banner">
          <GitBranch size={17} />
          远程仓库还没有提交。可选“本地项目源码”验证代码分析，后续再同步远程版本。
        </div>
      )}
      {tree && (
        <>
          <section className="panel code-query">
            <div className="panel-heading">
              <h3>源码检索</h3>
              <span className="small-muted">固定提交 · BM25 与标识符</span>
            </div>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                search();
              }}
            >
              <div className="search-input">
                <Search size={17} />
                <input
                  aria-label="源码关键词"
                  placeholder="例如：stream events、renew_lease、Worker…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  maxLength={1000}
                />
              </div>
              <input
                aria-label="代码检索目录"
                placeholder="限定目录（可选）"
                value={prefix}
                onChange={(e) => setPrefix(e.target.value)}
                maxLength={400}
              />
              <button
                className="button primary"
                disabled={pending || !query.trim()}
              >
                <Search size={15} />
                检索源码
              </button>
            </form>
            {searchResult && (
              <div className="code-search-results">
                <p className="small-muted">
                  检索 {searchResult.searched_files} 个文件 · 命中{" "}
                  {searchResult.results.length} 个片段 · {searchResult.searched_chunks || 0} 个候选片段
                </p>
                <p className="small-muted">{searchResult.coverage}</p>
                {!!(searchResult.omitted_files || searchResult.unreadable_files || searchResult.omitted_lines || searchResult.parse_fallback_files) && <p className="review-note">
                  {searchResult.omitted_files} 个文件超出范围，{searchResult.unreadable_files || 0} 个文件非可读文本，{searchResult.omitted_lines || 0} 行过长未纳入；{searchResult.parse_fallback_files || 0} 个 Python 文件无法解析，已按行检索。
                </p>}
                {!searchResult.results.length && (
                  <p>没有找到匹配内容，请使用具体名称、路径或代码中的关键词。</p>
                )}
                {searchResult.results.map((hit) => (
                  <button
                    className="code-search-hit"
                    key={hit.id}
                    onClick={() =>
                      read(
                        hit.path,
                        Math.max(1, hit.line_start - 5),
                        searchResult,
                      )
                    }
                    disabled={pending}
                  >
                    <div>
                      <FileText size={14} />
                      <strong>{hit.citation}</strong>
                      <span>得分 {hit.score?.toFixed(1)}</span>
                      <ArrowUpRight size={14} />
                    </div>
                    <p className="code-symbol-meta">{hit.symbol ? `${hit.symbol} · Python 定义${hit.partial_symbol ? " · 部分行段" : ""}` : "文本行段"}
                      {hit.matched_terms?.length ? ` · 匹配 ${hit.matched_terms.slice(0, 6).join("、")}` : ""}
                    </p>
                    <pre>{hit.content}</pre>
                  </button>
                ))}
              </div>
            )}
          </section>
          <div className="code-browser">
            <section className="panel code-tree">
              <div className="panel-heading">
                <h3>文件列表</h3>
                <span className="muted-count">{tree.files.length}</span>
              </div>
              <input
                aria-label="筛选代码文件"
                placeholder="按路径筛选…"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              />
              <div className="code-file-list">
                {tree.files
                  .filter((x) =>
                    x.path.toLowerCase().includes(filter.toLowerCase()),
                  )
                  .map((item) => (
                    <button
                      key={item.path}
                      className={file?.path === item.path ? "selected" : ""}
                      onClick={() => read(item.path)}
                      disabled={pending}
                    >
                      <FileText size={14} />
                      <span>{item.path}</span>
                    </button>
                  ))}
              </div>
            </section>
            <section className="panel code-viewer">
              {file ? (
                <>
                  <div className="panel-heading">
                    <h3>
                      <Code2 size={17} />
                      {file.path}
                    </h3>
                    {file.url && (
                      <a
                        href={file.url}
                        target="_blank"
                        rel="noreferrer"
                        aria-label="查看 GitHub 源码"
                      >
                        <ExternalLink size={15} />
                      </a>
                    )}
                  </div>
                  <div className="code-file-meta">
                    <code>
                      {file.sha?.slice(0, 12)} · L{file.line_start}-
                      {file.line_end}
                    </code>
                    <span>
                      {file.source === "local_project"
                        ? "本地项目快照"
                        : "GitHub"}
                      {file.redacted ? " · 已隐藏密钥值" : ""}
                    </span>
                  </div>
                  <div className="code-lines">
                    {file.content.split("\n").map((line, i) => (
                      <div key={i}>
                        <span>{file.line_start + i}</span>
                        <code>{line || " "}</code>
                      </div>
                    ))}
                  </div>
                  <div className="code-file-footer">
                    <button
                      className="text-button"
                      disabled={pending || file.line_start === 1}
                      onClick={() =>
                        read(
                          file.path,
                          Math.max(1, file.line_start - 200),
                          file,
                        )
                      }
                    >
                      <ArrowLeft size={14} />
                      上一段
                    </button>
                    <button
                      className="text-button"
                      disabled={pending || file.line_end >= file.total_lines}
                      onClick={() => read(file.path, file.line_end + 1, file)}
                    >
                      下一段
                      <ArrowRight size={14} />
                    </button>
                    <button
                      className="button secondary"
                      disabled={busy || pending}
                      onClick={() =>
                        onAsk(
                          `结合源码和项目文档，分析 ${file.path} 中的关键实现与证据缺口。请生成任务计划并引用路径、行号和提交。`,
                        )
                      }
                    >
                      协作分析此文件
                      <ArrowUpRight size={14} />
                    </button>
                  </div>
                </>
              ) : (
                <div className="code-viewer-empty">
                  <Code2 size={34} />
                  <h3>选择一个文件</h3>
                  <p>查看当前提交的源码及来源行号。</p>
                </div>
              )}
            </section>
          </div>
          <p className="small-muted">
            {tree.coverage}本次不执行仓库代码或测试。
            {tree.omitted_files > 0
              ? `有 ${tree.omitted_files} 个文件未纳入读取范围。`
              : ""}
          </p>
        </>
      )}
    </div>
  );
}
