export type Repository = {
  id: string;
  full_name: string;
  source: string;
  synced_at: string | null;
};
export type Issue = {
  number: number;
  title: string;
  state: string;
  labels: string[];
  body: string;
  url: string;
};
export type Pull = {
  number: number;
  title: string;
  state: string;
  body: string;
  head_sha: string;
  url: string;
};
export type CI = {
  id: number;
  name: string;
  status: string;
  conclusion: string | null;
  head_sha: string;
  url: string;
};
export type Doc = { id: string; title: string; content: string; url: string };
export type Snapshot = {
  name: string;
  description: string;
  branch: string;
  source: string;
  coverage: string;
  issues: Issue[];
  pulls: Pull[];
  runs: CI[];
  documents: Doc[];
  synced_at: string | null;
  sync_info?: { empty_repository: boolean; etag_cache_hits: number; strategy?:string;trigger?:string;refreshed_sections?:string[];section_times?:Record<string,string> };
};
export type Evidence = {
  id: string;
  title: string;
  content: string;
  url: string;
  sha?: string;
  source: string;
  citation?: string;
  methods?: string[];
};
export type Analysis = {
  task_skill?: {id:string;name:string;version:string;definition_hash:string;checklist:string[];mode:"demo-rules"|"live"};
  analysis_context?: {prompt_id:string;prompt_name:string;template_hash:string};
  fact_review?: FactReview;
  title: string;
  summary: string;
  recommendation: string;
  findings: {
    severity: string;
    title: string;
    detail: string;
    evidence_ids: string[];
  }[];
  next_steps: string[];
  gaps: string[];
  evidence: Evidence[];
  provider: string;
  task: string;
  workflow?: {
    plans: TaskPlan[];
    outcomes: TaskOutcome[];
    replan_count: number;
    synthesis_provider: string;
    workspace: { source: string; sha: string } | null;
    resume_count?: number;
  };
};
export type SourceFact = {name:string;value:string;subject:string;kind:string;statement?:string;evidence_id:string;path:string;sha:string;line:number};
export type FactCheck = {field:string;claim:string;observed:string;name:string;status:"matched"|"conflict"|"insufficient";reason:string;expected:SourceFact[];original_text:string;kind?:"numeric"|"bm25_relation";task_id?:string;task_title?:string};
export type FactReview = {checker:string;checker_revision:string;input_hash:string;source_hash:string;scope:string;status:"conflict"|"partial"|"unavailable";counts:Record<"matched"|"conflict"|"insufficient",number>;categories?:Record<"numeric"|"bm25_relation",Record<"matched"|"conflict"|"insufficient",number>>;checks:FactCheck[];facts:SourceFact[];relations?:SourceFact[];limited:boolean;preview?:boolean;original_analysis?:Pick<Analysis,"title"|"summary"|"recommendation"|"findings"|"next_steps"|"gaps">|null};
export type Run = {
  id: string;
  question: string;
  task: string;
  status: string;
  created_at: string;
  conversation_id: string;
  mode: string;
  result: Analysis | null;
  recovery?: RunRecovery | null;
};
export type TaskSkill = {id:string;name:string;version:string;definition_hash:string;description:string;task:"code"|"pr"|"ci"|"workflow";requires_target:boolean;checklist:string[];instructions:string;allowed_tools:string[];roles:string[]};
export type RunRecovery = {
  available: boolean;
  reason: string;
  phase: string;
  resume_count: number;
  max_resumes?: number;
  settled_tasks: number;
  total_tasks: number;
  plans: TaskPlan[];
  outcomes: TaskOutcome[];
  workspace: { source: string; sha: string } | null;
};
export type RunDetail = {
  id: string;
  question: string;
  status: string;
  conversation_id: string;
  events: StreamEvent[];
  result: Analysis | null;
  recovery: RunRecovery | null;
  task: string;
  background: boolean;
  stop_requested: boolean;
};
export type RunSubmission = { run_id: string; conversation_id: string; status: string };
export type QueueStatus = { backend: string; workers_online: number; concurrency: number; queued: number; running: number; lease_seconds: number };

class RunSubscriptionError extends Error {}

export async function watchRun(
  repositoryId: string, runId: string, signal: AbortSignal,
  onEvent: (event: StreamEvent) => void, onReconnect: () => void, after = 0,
) {
  let cursor = after;
  const root = `/repositories/${repositoryId}/runs/${runId}`;
  while (!signal.aborted) {
    try {
      const response = await fetch(`/api${root}/events?after=${cursor}`, { signal });
      if (response.status===401) window.dispatchEvent(new Event("devflow:unauthorized"));
      if ([400, 401, 403, 404, 422].includes(response.status)) throw new RunSubscriptionError("运行记录不可访问，请刷新运行记录。");
      if (!response.ok || !response.body) throw new Error("暂时无法订阅运行进度");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let pending = "";
      try {
        while (true) {
          const { value, done } = await reader.read();
          pending += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
          let split: number;
          while ((split = pending.indexOf("\n\n")) !== -1) {
            const frame = pending.slice(0, split);
            pending = pending.slice(split + 2);
            const data = frame.split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
            if (data) {
              const event: StreamEvent = JSON.parse(data);
              if (event.sequence > cursor) {
                onEvent(event);
                cursor = event.sequence;
              }
            }
          }
          if (done) break;
        }
      } finally { reader.releaseLock(); }
      const detail = await api<RunDetail>(root, { signal });
      if (!["queued", "running"].includes(detail.status)) return;
    } catch (e) {
      if (signal.aborted || e instanceof RunSubscriptionError) throw e;
      onReconnect();
    }
    await new Promise<void>((resolve, reject) => {
      const onAbort = () => { clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); };
      const timer = setTimeout(() => { signal.removeEventListener("abort", onAbort); resolve(); }, 1500);
      signal.addEventListener("abort", onAbort, { once: true });
      if (signal.aborted) { signal.removeEventListener("abort", onAbort); onAbort(); }
    });
  }
}
export type StreamEvent = {
  type: string;
  sequence: number;
  data: Record<string, unknown>;
};
export type Health = {
  mode: string;
  model: string;
  storage: string;
  retrieval: string;
  external_writes: boolean;
};
export type Draft = {
  id: string;
  body: string;
  status: string;
  run_id: string;
  created_at: string;
  version: number;
  target_kind: "issue" | "pr" | null;
  target_number: number | null;
  expected_sha: string | null;
  approved_by: string | null;
  note: string;
  published_url: string | null;
  publish_message: string;
};

export type AuthState = { auth_enabled: boolean; user: { id: string; username: string; is_admin: boolean; authenticated: boolean } | null };
export type ProjectMemory = { id:string; title:string; content:string; run_id:string|null; evidence_ids:string[]; status:string; version:number; author:string; approved_by:string|null; updated_at:string };
export type Governance = { role:string; can_edit:boolean; can_review:boolean; publish_enabled:boolean; auth_enabled:boolean };
export type AuditEntry = { id:string; actor:string; action:string; object_id:string; detail:Record<string,unknown>; created_at:string };

export type KnowledgeDocument = {
  id: string;
  title: string;
  path: string;
  content: string;
  source: string;
  url: string;
  revision: string | null;
  chunk_count: number;
  updated_at: string;
};
export type KnowledgeStatus = {
  embedding_execution: "local-cpu" | "remote";
  vector_store: "milvus-lite" | "milvus";
  document_count: number;
  chunk_count: number;
  default_mode: string;
  vector_configured: boolean;
  vector_ready: boolean;
  embedding_model: string | null;
  rerank_enabled: boolean;
  index: {
    status: string;
    indexed_chunks: number;
    dimension: number | null;
    message: string;
    updated_at: string | null;
  };
};
export type KnowledgeHit = {
  source_scope?: string;
  source_notice?: string;
  id: string;
  chunk_id: string;
  title: string;
  content: string;
  heading: string;
  citation: string;
  path: string;
  line_start: number;
  line_end: number;
  revision: string | null;
  source: string;
  url: string;
  methods: string[];
  score: number;
  bm25_score?: number;
  vector_score?: number;
  rerank_score?: number;
};
export type KnowledgeSearch = {
  query: string;
  mode: string;
  reranked: boolean;
  results: KnowledgeHit[];
  warnings: string[];
  candidate_count: number;
};

export type PlanStep = {
  id: string;
  agent: string;
  title: string;
  query: string;
  target: number | null;
  depends_on: string[];
};
export type TaskPlan = {
  reason: string;
  steps: PlanStep[];
  provider: string;
  round: number;
};
export type TaskOutcome = PlanStep & {
  status: string;
  summary: string;
  gaps: string[];
  actual_target?: number | null;
  evidence_ids: string[];
};
export type CodeStatus = {
  source: string | null;
  status: string;
  sha: string | null;
  file_count: number;
  message: string;
  updated_at: string | null;
  local_import_available: boolean;
};
export type CodeFiles = {
  sha: string;
  source: string;
  files: { path: string; size: number; blob: string }[];
  omitted_files: number;
  coverage: string;
};
export type CodeHit = Evidence & {
  path: string;
  line_start: number;
  line_end: number;
  total_lines: number;
  redacted: boolean;
  score?: number;
  symbol?: string | null;
  symbol_start?: number | null;
  symbol_end?: number | null;
  partial_symbol?: boolean;
  chunk_kind?: string;
  matched_terms?: string[];
  retrieval_method?: string;
};
export type CodeSearch = {
  query: string;
  sha: string;
  source: string;
  results: CodeHit[];
  searched_files: number;
  searched_chunks?: number;
  omitted_files: number;
  unreadable_files?: number;
  omitted_lines?: number;
  parse_fallback_files?: number;
  retrieval_method?: string;
  coverage: string;
};

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event("devflow:unauthorized"));
    const body = await response.json().catch(() => ({}));
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : `请求失败（${response.status}）`,
    );
  }
  return response.json();
}

export async function streamAnalysis(
  payload: object,
  signal: AbortSignal,
  onEvent: (event: StreamEvent) => void,
  path = "/chat/stream",
) {
  const response = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok || !response.body) {
    const data = await response.json().catch(() => ({}));
    throw new Error(
      typeof data.detail === "string" ? data.detail : "无法启动分析",
    );
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  let ended = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      pending += decoder
        .decode(value, { stream: !done })
        .replace(/\r\n/g, "\n");
      let split: number;
      while ((split = pending.indexOf("\n\n")) !== -1) {
        const frame = pending.slice(0, split);
        pending = pending.slice(split + 2);
        const data = frame
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (data) {
          const event: StreamEvent = JSON.parse(data);
          if (
            event.type === "run.completed" ||
            event.type === "run.failed" ||
            event.type === "run.cancelled"
          )
            ended = true;
          onEvent(event);
        }
      }
      if (done) break;
    }
    if (!ended) throw new Error("连接已中断，分析没有完成，请查看运行记录。");
  } finally {
    reader.releaseLock();
  }
}
