"use client";

import { useEffect, useState } from "react";
import {
  BookOpen,
  Database,
  FileText,
  LoaderCircle,
  Plus,
  Search,
  X,
  ArrowUpRight,
  ExternalLink,
} from "lucide-react";
import {
  api,
  KnowledgeDocument,
  KnowledgeSearch,
  KnowledgeStatus,
} from "@/lib/api";

function serviceLabel(status: string) {
  return ({ready:"正常",unreachable:"无法连接",mismatch:"版本不符",not_configured:"未配置",not_probed:"未探测"} as Record<string,string>)[status] || "待检查";
}

export function KnowledgePanel({
  repositoryId,
  busy,
  onAsk,
}: {
  repositoryId: string;
  busy: boolean;
  onAsk: (question: string) => void;
}) {
  const [status, setStatus] = useState<KnowledgeStatus | null>(null);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState("keyword");
  const [result, setResult] = useState<KnowledgeSearch | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [showImport, setShowImport] = useState(false);
  const [title, setTitle] = useState("");
  const [path, setPath] = useState("docs/notes.md");
  const [content, setContent] = useState("");
  const [services,setServices]=useState<{embedding:{status:string;message:string};milvus:{status:string;message:string;index_present?:boolean|null}}|null>(null);
  const [checking,setChecking]=useState(false);
  const root = `/repositories/${repositoryId}/knowledge`;

  async function refresh() {
    const [next, docs] = await Promise.all([
      api<KnowledgeStatus>(`${root}/status`),
      api<KnowledgeDocument[]>(`${root}/documents`),
    ]);
    setStatus(next);
    setDocuments(docs);
    if (!next.vector_ready) setMode("keyword");
  }
  useEffect(() => {
    let alive = true;
    Promise.all([
      api<KnowledgeStatus>(`${root}/status`),
      api<KnowledgeDocument[]>(`${root}/documents`),
    ])
      .then(([next, docs]) => {
        if (alive) {
          setStatus(next);
          setDocuments(docs);
        }
      })
      .catch((e) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [root]);
  useEffect(() => {
    if (status?.index.status !== "indexing") return;
    let alive = true;
    const timer = setInterval(
      () =>
        api<KnowledgeStatus>(`${root}/status`)
          .then((next) => {
            if (alive) setStatus(next);
          })
          .catch((e) => {
            if (alive) setError(e.message);
          }),
      2500,
    );
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [root, status?.index.status]);

  async function search() {
    setPending(true);
    setError("");
    setNotice("");
    setResult(null);
    try {
      setResult(
        await api<KnowledgeSearch>(`${root}/search`, {
          method: "POST",
          body: JSON.stringify({ query, mode, top_k: 5 }),
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }
  async function save() {
    setPending(true);
    setError("");
    try {
      const saved = await api<{ changed: boolean }>(`${root}/documents`, {
        method: "POST",
        body: JSON.stringify({ title, path, content }),
      });
      await refresh();
      setResult(null);
      setShowImport(false);
      setContent("");
      setTitle("");
      setNotice(
        saved.changed
          ? "文档已保存并切分，可立即使用 BM25 检索。"
          : "文档内容未变化，已保留现有片段。",
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }
  async function index() {
    setPending(true);
    setError("");
    try {
      const next = await api<{ status: KnowledgeStatus }>(`${root}/index`, {
        method: "POST",
      });
      setStatus(next.status);
      setNotice("索引任务已提交，可在这里查看进度。");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }
  async function checkServices(){
    setChecking(true);setError("");
    try{setServices(await api<NonNullable<typeof services>>(`${root}/services`));}catch(e){setError((e as Error).message);}finally{setChecking(false);}
  }
  async function archive(doc: KnowledgeDocument) {
    setPending(true);
    setError("");
    try {
      await api(`${root}/documents/${doc.id}/archive`, { method: "POST" });
      await refresh();
      setResult(null);
      setNotice("文档已从检索范围移除。按相同路径重新导入即可恢复。");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="knowledge-page">
      <section className="panel knowledge-summary">
        <div className="knowledge-stat">
          <BookOpen size={20} />
          <strong>{status?.document_count ?? "—"}</strong>
          <span>知识文档</span>
        </div>
        <div className="knowledge-stat">
          <FileText size={20} />
          <strong>{status?.chunk_count ?? "—"}</strong>
          <span>可引用片段</span>
        </div>
        <div className="knowledge-vector">
          <span className="tag">
            <Database size={13} />{" "}
            {status?.vector_ready
              ? `${status.vector_store === "milvus-lite" ? "Milvus Lite" : "Milvus"} 索引就绪`
              : status?.index.status === "indexing"
                ? "正在建立索引"
                : "BM25 已可用"}
          </span>
          <p>{status?.index.message || "正在读取知识库…"}</p>
          {status?.vector_configured && <p className="small-muted">{status.embedding_execution === "local-cpu" ? "本机中文模型 · CPU 离线计算" : "已配置的 Embedding 服务"}{status.index.dimension ? ` · ${status.index.dimension} 维` : ""}</p>}
        </div>
        <button
          className="button secondary"
          onClick={index}
          disabled={
            pending ||
            !status?.vector_configured ||
            status.index.status === "indexing"
          }
          title={
            !status?.vector_configured
              ? "需在 backend/.env 配置 Embedding 与 Milvus"
              : undefined
          }
        >
          <Database size={15} />
          建立向量索引
        </button>
      </section>
      {status?.vector_configured && <section className="panel knowledge-connection"><div className="panel-heading"><h3>向量服务连接</h3><button className="button secondary" disabled={checking} onClick={checkServices}>{checking?<LoaderCircle size={14} className="spin"/>:<Database size={14}/>}检查向量连接</button></div><p className="small-muted">索引状态按保存的文档和模型版本核对；连接检查只读取服务状态。</p>{services&&<div className="knowledge-service-results"><p><span className="tag">Embedding {serviceLabel(services.embedding.status)}</span>{services.embedding.message}</p><p><span className="tag">Milvus {serviceLabel(services.milvus.status)}</span>{services.milvus.message}{services.milvus.index_present===false?" 当前集合不存在，请重建索引。":""}</p></div>}</section>}
      {error && (
        <div className="knowledge-message error" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="knowledge-message" role="status">
          {notice}
        </div>
      )}
      <section className="panel knowledge-query">
        <div className="panel-heading">
          <h3>检索实验台</h3>
          <span className="small-muted">查询当前仓库 · 返回来源与行号</span>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            search();
          }}
          className="knowledge-search-form"
        >
          <div className="search-input">
            <Search size={17} />
            <input
              aria-label="知识查询"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="例如：如何启动后端、证据引用、发布检查…"
              maxLength={1000}
            />
          </div>
          <select
            aria-label="知识检索方式"
            value={mode}
            onChange={(e) => setMode(e.target.value)}
          >
            <option value="keyword">BM25 关键词</option>
            <option value="hybrid" disabled={!status?.vector_ready}>
              BM25 + 向量 / RRF
            </option>
          </select>
          <button
            className="button primary"
            disabled={pending || !query.trim()}
          >
            {pending ? (
              <LoaderCircle className="spin" size={15} />
            ) : (
              <Search size={15} />
            )}
            检索
          </button>
        </form>
        {result && (
          <div className="knowledge-results">
            <p className="small-muted">
              召回 {result.results.length} 个片段 ·{" "}
              {result.mode === "hybrid" ? "混合召回 / RRF" : "BM25"}
              {result.reranked ? " · 已重排" : ""}
              {" · 得分表示排序，不是结论置信度"}
            </p>
            {result.warnings.map((x) => (
              <p className="knowledge-message" key={x}>
                {x}
              </p>
            ))}
            {!result.results.length && (
              <p className="empty-small">
                没有匹配的片段。可以换用文档里的关键词，或导入更多资料。
              </p>
            )}
            {result.results.map((hit, i) => (
              <article className="knowledge-hit" key={hit.chunk_id}>
                <div>
                  <span className="knowledge-rank">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <h4>
                    {hit.title} <span>{hit.heading}</span>
                  </h4>
                  <span className="tag">{hit.methods.join(" + ")}</span>
                </div>
                {hit.source_notice && <p className="knowledge-message">{hit.source_notice}</p>}
                <pre>{hit.content}</pre>
                <footer>
                  <code>{hit.citation}</code>
                  <span>
                    {hit.source === "manual"
                      ? "本地导入"
                      : hit.source === "demo"
                        ? "演示文档"
                        : "GitHub"}{" "}
                    · {hit.revision?.slice(0, 8) || "未标记版本"} · 得分{" "}
                    {(hit.rerank_score ?? hit.score).toFixed(4)}
                  </span>
                </footer>
                {hit.url && (
                  <a
                    className="source-link"
                    href={hit.url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    查看来源 <ExternalLink size={13} />
                  </a>
                )}
              </article>
            ))}
          </div>
        )}
      </section>
      <div className="knowledge-library-heading">
        <div>
          <h3>文档库</h3>
          <p>相同路径重新导入会更新版本，旧片段随之失效。</p>
        </div>
        <button
          className="button secondary"
          onClick={() => {
            setShowImport(!showImport);
            setError("");
          }}
          disabled={pending}
        >
          <Plus size={15} />
          导入 Markdown
        </button>
      </div>
      {showImport && (
        <form
          className="panel knowledge-import"
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <div className="panel-heading">
            <h3>导入项目文档</h3>
            <button
              type="button"
              className="icon-button"
              aria-label="关闭文档导入"
              onClick={() => setShowImport(false)}
            >
              <X size={18} />
            </button>
          </div>
          <div className="knowledge-import-fields">
            <label>
              文档标题
              <input
                required
                aria-label="文档标题"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                maxLength={200}
                placeholder="如：开发与发布指南"
              />
            </label>
            <label>
              相对路径
              <input
                required
                aria-label="文档相对路径"
                value={path}
                onChange={(e) => setPath(e.target.value)}
                maxLength={400}
              />
            </label>
          </div>
          <label>
            Markdown 内容
            <textarea
              required
              aria-label="Markdown 内容"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={10}
              maxLength={100000}
              placeholder="# 项目约定\n在这里粘贴文档内容…"
            />
          </label>
          <div>
            <span className="small-muted">
              保存在本地知识库，来源标为“本地导入”。
            </span>
            <button
              className="button primary"
              disabled={pending || !title.trim() || !content.trim()}
            >
              保存并切分
            </button>
          </div>
        </form>
      )}
      <div className="documents-grid">
        {documents.map((doc) => (
          <article className="panel document-card" key={doc.id}>
            <span className="document-icon">
              <FileText size={23} />
            </span>
            <span className="document-type">
              {doc.source === "manual"
                ? "本地导入"
                : doc.source === "demo"
                  ? "演示文档"
                  : "GITHUB DOCUMENT"}{" "}
              · {doc.chunk_count} 个片段
            </span>
            <h3>{doc.title}</h3>
            <pre>
              {doc.content.slice(0, 450)}
              {doc.content.length > 450 ? "…" : ""}
            </pre>
            <div>
              <code>{doc.path}</code>
              <button
                onClick={() =>
                  onAsk(
                    `根据项目文档解释「${doc.title}」的关键要求，并引用证据。`,
                  )
                }
                disabled={busy}
              >
                提问 <ArrowUpRight size={14} />
              </button>
            </div>
            {doc.source === "manual" && (
              <button
                className="text-button archive-document"
                onClick={() => archive(doc)}
                disabled={pending}
              >
                移出知识库
              </button>
            )}
          </article>
        ))}
      </div>
      {!documents.length && (
        <div className="empty-large">
          <BookOpen size={30} />
          <h2>还没有项目文档</h2>
          <p>
            同步会读取仓库 README；也可以导入本地 Markdown，马上开始知识检索。
          </p>
        </div>
      )}
      {!status?.vector_configured && (
        <div className="info-banner">
          <Database size={17} />
          向量召回需要单独的 Embedding 服务和 Milvus。DeepSeek
          用于生成分析；当前检索使用 BM25。
        </div>
      )}
    </div>
  );
}
