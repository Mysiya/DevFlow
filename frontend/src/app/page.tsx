"use client";

import { useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Bot,
  Check,
  CheckCheck,
  ChevronDown,
  CircleCheck,
  CircleDot,
  Code2,
  Command,
  Copy,
  ExternalLink,
  FileText,
  GitBranch,
  GitPullRequest,
  History,
  Layers3,
  LoaderCircle,
  MessageSquare,
  Plus,
  RefreshCw,
  Send,
  Settings2,
  ShieldCheck,
  SquareTerminal,
  TriangleAlert,
  X,
  FlaskConical,
} from "lucide-react";
import {
  Analysis,
  api,
  CI,
  Draft,
  Health,
  Repository,
  Run,
  RunRecovery,
  RunDetail,
  Snapshot,
  StreamEvent,
  watchRun,
  RunSubmission,
  QueueStatus,
  AuthState,
} from "@/lib/api";

import { KnowledgePanel } from "@/components/knowledge-panel";
import { CodePanel } from "@/components/code-panel";
import { WorkflowPlan } from "@/components/workflow-plan";
import { RecoveryPanel } from "@/components/recovery-panel";
import { GovernancePanel } from "@/components/governance-panel";
import { LoginPanel } from "@/components/login-panel";
import { DeliveryPanel } from "@/components/delivery-panel";
import { AnalysisPromptSelect } from "@/components/answer-comparison";
import { TaskSkillSelect, TaskSkillPreview } from "@/components/task-skill-select";
import type { TaskSkill } from "@/lib/api";
import { SyncPanel } from "@/components/sync-panel";
import { FactReviewPanel } from "@/components/fact-review-panel";
import { MobileNavigation } from "@/components/mobile-navigation";
import { MobileInstallPanel, useMobileApp } from "@/components/mobile-app-provider";

type Page =
  "overview" | "workspace" | "code" | "knowledge" | "history" | "settings" | "governance" | "delivery";
type Task =
  "chat" | "issue" | "pr" | "ci" | "workflow" | "knowledge" | "code" | "report";
const titles: Record<Page, string> = {
  overview: "仓库概览",
  workspace: "Agent 工作台",
  code: "代码工作区",
  knowledge: "项目知识",
  history: "运行记录",
  settings: "连接与配置",
  governance: "记忆与审批",
  delivery: "评测与交付",
};
const taskLabels: Record<string, string> = {
  chat: "智能问答",
  issue: "Issue 分诊",
  pr: "PR 风险审查",
  ci: "CI 排障",
  workflow: "协作检查",
  knowledge: "知识检索",
  code: "源码分析",
  report: "快照报告",
};
const severityLabels: Record<string, string> = {
  high: "高风险",
  medium: "待关注",
  low: "低风险",
  info: "信息",
};

function date(value: string | null) {
  if (!value) return "尚未同步";
  const parsed = new Date(
    value.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(value) ? value : value + "Z",
  );
  return parsed.toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function eventLabel(event: StreamEvent) {
  const data = event.data;
  if (event.type === "run.started") return "已创建分析记录";
  if (event.type === "run.queued") return "任务已保存到后台队列";
  if (event.type === "run.interrupted") return String(data.message);
  if (event.type === "run.resumed")
    return `已从断点恢复 · 保留 ${data.retained_tasks} 项任务结果`;
  if (event.type === "run.cancelled") return String(data.message || "运行已停止，已有记录保留。");
  if (event.type === "task.reused") return `复用已保存结果 · ${data.title}`;
  if (event.type === "answer.reused") return String(data.message);
  if (event.type === "plan") return "Planner · 已生成检查计划";
  if (event.type === "planner.repair") return "Planner · 修正一次任务计划";
  if (event.type === "planner.fallback")
    return `使用模板计划 · ${data.message}`;
  if (event.type === "task.started") return `执行任务 · ${data.title}`;
  if (event.type === "task.completed")
    return `${data.title} · ${data.status === "partial" ? "证据不足" : "已完成"}`;
  if (event.type === "task.failed")
    return `任务未完成 · ${data.title} · ${data.message}`;
  if (event.type === "task.skipped") return `跳过任务 · ${data.title}`;
  if (event.type === "replan.started") return "Planner · 开始一次有限补查";
  if (event.type === "replan.failed" || event.type === "synthesis.fallback")
    return String(data.message);
  if (event.type === "tool.started") return `调用工具 · ${data.tool}`;
  if (event.type === "tool.completed") return `工具完成 · ${data.tool}`;
  if (event.type === "agent.started")
    return `开始 ${taskLabels[String(data.agent)] || data.agent}`;
  if (event.type === "agent.completed")
    return `${taskLabels[String(data.agent)] || data.agent}完成 · ${data.findings} 项发现`;
  if (event.type === "agent.failed") return `任务未完成 · ${data.message}`;
  if (event.type === "retrieval.warning") return `检索提示 · ${data.message}`;
  if (event.type === "observer") return "Observer · 已检查证据缺口";
  if (event.type === "run.completed") return "分析完成，结果已保存";
  if (event.type === "run.failed") return `分析失败 · ${data.message}`;
  return event.type;
}

export default function Home() {
  const mobileApp=useMobileApp();
  const [page, setPage] = useState<Page>("overview");
  const [health, setHealth] = useState<Health | null>(null);
  const [authState,setAuthState] = useState<AuthState|null>(null);
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [repositoryId, setRepositoryId] = useState("");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [question, setQuestion] = useState("");
  const [task, setTask] = useState<Task>("chat");
  const [promptId,setPromptId] = useState("baseline-v1");
  const [selectedSkill,setSelectedSkill] = useState<TaskSkill|null>(null);
  const [skillTarget,setSkillTarget] = useState("");
  useEffect(()=>{setSelectedSkill(null);setSkillTarget("");},[repositoryId]);
  const [submitted, setSubmitted] = useState("");
  const [result, setResult] = useState<Analysis | null>(null);
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [delta, setDelta] = useState("");
  const [runId, setRunId] = useState("");
  const [runStatus, setRunStatus] = useState("");
  const [recovery, setRecovery] = useState<RunRecovery | null>(null);
  const [stopping, setStopping] = useState(false);
  const [queueStatus, setQueueStatus] = useState<QueueStatus | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [listTab, setListTab] = useState<"issues" | "pulls" | "runs">("issues");
  const [filter, setFilter] = useState("");
  const [showAdd, setShowAdd] = useState(false);
  const [newRepo, setNewRepo] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    let alive = true;
    api<AuthState>("/auth/session").then(async auth=>{
      if (!alive) return null;
      setAuthState(auth);
      if (auth.auth_enabled&&!auth.user) {setLoading(false);return null;}
      return Promise.all([api<Health>("/health"), api<Repository[]>("/repositories")]);
    })
      .then(data => {
        if (!alive || !data) return;
        const [state,repos] = data;
        setHealth(state);
        setRepositories(repos);
        setRepositoryId(repos[0]?.id || "");
        if (!repos.length) setLoading(false);
      })
      .catch((e) => {
        if (alive) {
          setError(`后端连接失败：${e.message}。请确认 FastAPI 已启动。`);
          setLoading(false);
        }
      });
    return () => {
      alive = false;
      abortRef.current?.abort();
    };
  }, []);

  useEffect(()=>{
    const expired=()=>{abortRef.current?.abort();setAuthState(previous=>previous?{...previous,user:null}:previous);setResult(null);setSnapshot(null);};
    window.addEventListener("devflow:unauthorized",expired);
    return ()=>window.removeEventListener("devflow:unauthorized",expired);
  },[]);

  useEffect(() => {
    if (!repositoryId) return;
    let alive = true;
    setLoading(true);
    setError("");
    setResult(null);
    setEvents([]);
    setSubmitted("");
    setConversationId(null);
    setRunId("");
    setRunStatus("");
    setRecovery(null);
    setSnapshot(null);
    Promise.all([
      api<Snapshot>(`/repositories/${repositoryId}/snapshot`),
      api<Run[]>(`/repositories/${repositoryId}/runs`),
      api<Draft[]>(`/repositories/${repositoryId}/drafts`),
    ])
      .then(([snap, history, savedDrafts]) => {
        if (alive) {
          setSnapshot(snap);
          setRuns(history);
          setDrafts(savedDrafts);
        }
      })
      .catch((e) => {
        if (alive) setError(e.message);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [repositoryId]);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 4000);
    return () => clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    let alive = true;
    const refresh = () => {
      api<QueueStatus>("/queue/status").then((state) => { if (alive) setQueueStatus(state); }).catch(() => {});
      if (page === "history" && repositoryId)
        api<Run[]>(`/repositories/${repositoryId}/runs`).then((history) => { if (alive) setRuns(history); }).catch(() => {});
    };
    refresh();
    const timer = setInterval(refresh, 3000);
    return () => { alive = false; clearInterval(timer); };
  }, [page, repositoryId]);

  async function reloadRuns() {
    setRuns(await api<Run[]>(`/repositories/${repositoryId}/runs`));
  }

  async function sync() {
    if (!repositoryId || syncing) return;
    setSyncing(true);
    setError("");
    try {
      await api(`/repositories/${repositoryId}/sync`, { method: "POST" });
      setSnapshot(
        await api<Snapshot>(`/repositories/${repositoryId}/snapshot`),
      );
      setToast("仓库快照已同步");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSyncing(false);
    }
  }

  async function addRepo() {
    if (syncing) return;
    setSyncing(true);
    setError("");
    try {
      const added = await api<Repository>("/repositories", {
        method: "POST",
        body: JSON.stringify({ full_name: newRepo.trim() }),
      });
      setRepositories(await api<Repository[]>("/repositories"));
      setRepositoryId(added.id);
      setShowAdd(false);
      setNewRepo("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSyncing(false);
    }
  }

  async function analyze(selectedTask: Task, text: string, target?: number, skill?: TaskSkill|null) {
    if (!repositoryId || busy || !text.trim()) return;
    if (!mobileApp.online) {setError("当前设备离线，请联网后提交分析。");return;}
    if (skill?.requires_target && (!target || !Number.isSafeInteger(target) || target <= 0)) {
      setError("请明确填写 PR 编号或 CI run ID。");return;
    }
    if (!skill) {setSelectedSkill(null);setSkillTarget("");}
    setPage("workspace");
    setBusy(true);
    setError("");
    setSubmitted(text);
    setResult(null);
    setEvents([]);
    setDelta("");
    setRunId("");
    setRunStatus("queued");
    setRecovery(null);
    setQuestion("");
    try {
      const queued = await api<RunSubmission>("/chat/runs", {
        method: "POST",
        body: JSON.stringify({
          repository_id: repositoryId,
          conversation_id: conversationId,
          message: text.trim(),
          task: selectedTask,
          prompt_id: promptId,
          skill_id: skill?.id || null,
          target,
        }),
      });
      setRunId(queued.run_id);
      setConversationId(queued.conversation_id);
      await observeRun(queued.run_id);
    } catch (e) {
      setError((e as Error).message);
      setRunStatus("");
    } finally {
      setBusy(false);
    }
  }

  async function observeRun(id: string, after = 0) {
    setBusy(true);
    setReconnecting(false);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await watchRun(repositoryId, id, controller.signal, (event) => {
        setReconnecting(false);
        if (event.type !== "answer.delta")
          setEvents((previous) => previous.some((x) => x.sequence === event.sequence) ? previous : [...previous, event]);
        if (event.type === "run.queued") setRunStatus("queued");
        if (event.type === "run.started" || event.type === "run.resumed") {
          setRunStatus("running");
          if (event.type === "run.resumed")
            setRecovery((previous) => previous ? { ...previous, resume_count: Number(event.data.resume_count) } : null);
        }
        if (event.type === "answer.delta") setDelta((previous) => previous + String(event.data.text));
        if (event.type === "run.completed") { setResult(event.data.result as Analysis); setRunStatus("completed"); }
        if (event.type === "run.failed") { setError(String(event.data.message)); setRunStatus("failed"); }
        if (event.type === "run.cancelled") { setToast("后台任务已停止，已保存的进度保留。"); setRunStatus("cancelled"); }
        if (event.type === "run.interrupted") setRunStatus("interrupted");
      }, () => setReconnecting(true), after);
    } catch (e) {
      if ((e as Error).name !== "AbortError") setError((e as Error).message);
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      await refreshSelectedRun(id).catch(() => setToast("后台任务已保留，进度暂时无法刷新。"));
      await reloadRuns().catch(() => {});
      setBusy(false);
      setReconnecting(false);
      setStopping(false);
    }
  }

  function leaveAnalysis() {
    abortRef.current?.abort();
    setPage("history");
    setToast("已离开工作台，后台分析继续运行。");
  }

  async function openRun(run: Run) {
    if (busy) return;
    try {
      const detail = await api<RunDetail>(
        `/repositories/${repositoryId}/runs/${run.id}`,
      );
      setRunId(run.id);
      setConversationId(run.conversation_id);
      setSubmitted(run.question);
      setResult(detail.result);
      setRunStatus(detail.status);
      setTask(detail.task as Task);
      setSelectedSkill(null);setSkillTarget("");
      setRecovery(detail.recovery);
      setEvents(detail.events.filter((x) => x.type !== "answer.delta"));
      setDelta("");
      setError("");
      setPage("workspace");
      if (detail.background && ["queued", "running"].includes(detail.status))
        await observeRun(run.id, detail.events.at(-1)?.sequence || 0);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function refreshSelectedRun(id: string) {
    const detail = await api<RunDetail>(
      `/repositories/${repositoryId}/runs/${id}`,
    );
    setResult(detail.result);
    setEvents(detail.events.filter((x) => x.type !== "answer.delta"));
    setRunStatus(detail.status);
    setRecovery(detail.recovery);
    return detail;
  }

  async function returnToWorkspace() {
    setPage("workspace");
    if (!runId || busy || !["queued", "running"].includes(runStatus)) return;
    setBusy(true);
    setError("");
    try {
      const detail = await refreshSelectedRun(runId);
      if (detail.background && ["queued", "running"].includes(detail.status))
        await observeRun(runId, detail.events.at(-1)?.sequence || 0);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function resumeRun() {
    if (busy || !runId || !recovery?.available) return;
    setBusy(true);
    setRunStatus("queued");
    setResult(null);
    setDelta("");
    setError("");
    try {
      const after = Math.max(0, ...events.map((x) => x.sequence));
      await api<RunSubmission>(`/repositories/${repositoryId}/runs/${runId}/resume-background`, { method: "POST" });
      await observeRun(runId, after);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      await refreshSelectedRun(runId).catch(() =>
        setToast("运行记录暂时无法刷新"),
      );
      await reloadRuns().catch(() => setToast("运行记录暂时无法刷新"));
      setBusy(false);
    }
  }

  async function stopAnalysis() {
    if (stopping) return;
    setStopping(true);
    try {
      if (runId) {
        const detail = await api<RunDetail>(`/repositories/${repositoryId}/runs/${runId}/stop`, {
          method: "POST",
        });
        setToast(detail.status === "cancelled" ? "任务已停止。" : ["queued", "running"].includes(detail.status) ? "已请求停止，等待后台确认。" : "任务已结束。");
      }
    } catch (e) {
      setError((e as Error).message);
      setStopping(false);
    }
  }

  async function createDraft() {
    try {
      await api(`/repositories/${repositoryId}/drafts`, {
        method: "POST",
        body: JSON.stringify({ run_id: runId }),
      });
      setDrafts(await api<Draft[]>(`/repositories/${repositoryId}/drafts`));
      setToast("评论草稿已保存到运行记录页；未发送到 GitHub");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setToast("已复制");
    } catch {
      setError("浏览器暂时不允许复制，请手动选择文本。");
    }
  }

  function resetChat() {
    if (busy) return;
    setResult(null);
    setSubmitted("");
    setEvents([]);
    setDelta("");
    setRunId("");
    setRunStatus("");
    setRecovery(null);
    setConversationId(null);
    setError("");
    setQuestion("");
    setPage("workspace");
  }

  const demo = health?.mode === "demo";
  const failures =
    snapshot?.runs.filter((x) => x.conclusion === "failure").length || 0;
  const visibleEvents = events.filter((x) => x.type !== "tool.completed");
  const ready = !!snapshot && !loading;

  if (authState?.auth_enabled&&!authState.user) return <LoginPanel/>;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="DevFlow 首页">
          <span className="brand-symbol">
            <Layers3 size={22} />
          </span>
          <span>
            DevFlow<span className="brand-ai">AI</span>
          </span>
        </a>
        <div className="workspace-label">
          WORKSPACE <span>v0.18</span>
        </div>
        <div className="repository-switch">
          <GitBranch size={17} />
          <select
            aria-label="选择仓库"
            value={repositoryId}
            disabled={busy || loading}
            onChange={(e) => setRepositoryId(e.target.value)}
          >
            {repositories.length ? (
              repositories.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.full_name.split("/")[1]}
                </option>
              ))
            ) : (
              <option value="">连接你的仓库</option>
            )}
          </select>
          <ChevronDown size={14} />
        </div>
        <nav>
          {(
            [
              { id: "overview", icon: Activity },
              { id: "workspace", icon: Bot },
              { id: "code", icon: Code2 },
              { id: "knowledge", icon: BookOpen },
              { id: "history", icon: History },
              { id: "governance", icon: ShieldCheck },
              { id: "delivery", icon: FlaskConical },
              { id: "settings", icon: Settings2 },
            ] as const
          ).map((item) => (
            <button
              key={item.id}
              aria-label={titles[item.id]}
              className={`nav-item ${page === item.id ? "active" : ""}`}
              onClick={() => item.id === "workspace" ? returnToWorkspace() : setPage(item.id)}
            >
              <item.icon size={19} />
              <span>{titles[item.id]}</span>
              {item.id === "workspace" && (
                <span className="nav-new">AGENT</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-roadmap">
          <div className="roadmap-icon">
            <Code2 size={19} />
          </div>
          <strong>评测与交付建设中</strong>
          <p>
            分析、恢复与审批已接入
            <br />
            用评测验证，用周报沉淀
          </p>
          <div className="roadmap-track">
            <span />
          </div>
          <div className="roadmap-caption">
            阶段 5 / 5 <span>评测与交付</span>
          </div>
          <button onClick={() => setPage("settings")}>
            查看实现路线 <ArrowUpRight size={14} />
          </button>
        </div>
        <div className="sidebar-footer">
          <span className="avatar">D</span>
          <div>
            <strong>{authState?.user?.username||"本地开发环境"}</strong>
            <small>{authState?.auth_enabled?"已登录":"本地模式 · 未启用登录"}</small>
          </div>
          <span className="status-dot" />
        </div>
      </aside>

      <main className="main-shell">
        <header className="topbar">
          <div className="breadcrumbs">
            工作空间 <span>/</span> <strong>{titles[page]}</strong>
          </div>
          <div className="topbar-right">
            {authState?.auth_enabled&&<button className="text-button" onClick={async()=>{await api("/auth/logout",{method:"POST"});window.location.reload();}}>退出登录</button>}
            <span className={`mode-pill ${demo ? "demo" : "live"}`}>
              <span />
              {health ? (demo ? "演示模式" : "真实连接") : "连接中"}
            </span>
            <button
              className="icon-button"
              title="连接与配置"
              onClick={() => setPage("settings")}
            >
              <Settings2 size={18} />
            </button>
            <span className="avatar top-avatar">D</span>
          </div>
        </header>
        <div className="page-content">
          {error && (
            <div className="error-banner" role="alert">
              <TriangleAlert size={17} />
              <span>{error}</span>
              <button onClick={() => setError("")} aria-label="关闭错误">
                <X size={15} />
              </button>
            </div>
          )}

          <div className="page-heading">
            <div>
              <div className="eyebrow">
                {page === "overview"
                  ? "REPOSITORY INTELLIGENCE"
                  : "DEVELOPMENT WORKSPACE"}
              </div>
              <h1>
                {titles[page]}
                <span className="heading-dot">.</span>
              </h1>
              <p>
                {page === "overview"
                  ? "把分散的研发信息，变成有依据的下一步。"
                  : page === "workspace"
                    ? "让 Agent 收集证据，让你掌握每一步判断。"
                    : page === "code"
                      ? "读取固定提交的源码，保留路径、行号和版本。"
                      : page === "knowledge"
                        ? "基于项目文档检索，保留来源与证据。"
                        : page === "history"
                          ? "回看分析轨迹、结论和本地评论草稿。"
                          : page === "governance"
                            ? "批准可复用的项目经验，审核草稿并追踪操作。"
                          : page === "delivery"
                            ? "运行固定评测，查看实际用量，把已保存分析整理成周报。"
                          : "连接真实服务，逐步扩展项目能力。"}
              </p>
            </div>
            <div className="heading-actions">
              {page === "overview" && (
                <button
                  className="button secondary"
                  onClick={sync}
                  disabled={!ready || syncing || busy}
                >
                  <RefreshCw size={15} className={syncing ? "spin" : ""} />
                  {syncing ? "同步中" : "同步仓库"}
                </button>
              )}
              {page === "workspace" && (
                <button
                  className="button secondary"
                  onClick={resetChat}
                  disabled={busy}
                >
                  <Plus size={16} />
                  新会话
                </button>
              )}
              {(page === "overview" || page === "history") && (
                <button
                  className="button primary"
                  onClick={resetChat}
                  disabled={!ready || busy}
                >
                  <Plus size={16} />
                  发起分析
                </button>
              )}
            </div>
          </div>

          {loading ? (
            <div className="loading">
              <LoaderCircle className="spin" size={26} />
              <p>正在连接你的工作空间…</p>
            </div>
          ) : !snapshot && page !== "settings" ? (
            <div className="empty-large">
              <GitBranch size={34} />
              <h2>连接第一个仓库</h2>
              <p>在连接与配置页面添加 GitHub 仓库，即可开始分析。</p>
              <button
                className="button primary"
                onClick={() => setPage("settings")}
              >
                打开连接配置 <ArrowRight size={16} />
              </button>
            </div>
          ) : (
            <>
              {page === "overview" && snapshot && (
                <>
                  <section className="overview-hero">
                    <div className="hero-copy">
                      <span className="hero-tag">
                        <GitBranch size={14} />
                        {snapshot.name}
                      </span>
                      <h2>
                        看清风险，
                        <br />
                        再推进下一次发布。
                      </h2>
                      <p>
                        Issue、代码变更与 CI 汇聚在这里。
                        <br />
                        从一个问题开始，让分析过程清晰可追溯。
                      </p>
                      <button
                        className="button dark"
                        disabled={busy}
                        onClick={() =>
                          analyze(
                            "workflow",
                            "检查当前 Issue、PR 和失败 CI，判断这个版本是否可以发布。",
                          )
                        }
                      >
                        运行发布检查 <ArrowUpRight size={16} />
                      </button>
                    </div>
                    <div className="hero-flow">
                      <div className="flow-top">
                        <span className="flow-signal" />
                        <span>协作工作流</span>
                        <small>LANGGRAPH</small>
                      </div>
                      <div className="flow-agents">
                        {[
                          {
                            icon: CircleDot,
                            name: "Issue Agent",
                            sub: "需求与优先级",
                          },
                          {
                            icon: GitPullRequest,
                            name: "PR Agent",
                            sub: "变更与风险",
                          },
                          {
                            icon: SquareTerminal,
                            name: "CI Agent",
                            sub: "日志与根因",
                          },
                        ].map((a) => (
                          <div className="flow-agent" key={a.name}>
                            <a.icon size={20} />
                            <strong>{a.name}</strong>
                            <small>{a.sub}</small>
                          </div>
                        ))}
                      </div>
                      <div className="flow-connectors">
                        <span />
                        <span />
                        <span />
                      </div>
                      <div className="flow-result">
                        <ShieldCheck size={20} />
                        <div>
                          <strong>Observer + Synthesis</strong>
                          <small>核验证据 · 汇总结论</small>
                        </div>
                        <CheckCheck size={18} />
                      </div>
                      <div className="flow-footer">
                        <span>
                          <span className="status-dot" /> 按任务依赖调度
                        </span>
                        <span>后台协作</span>
                      </div>
                    </div>
                  </section>

                  <section className="metrics-grid">
                    {[
                      {
                        title: "快照内开放 Issue",
                        value: snapshot.issues.length,
                        icon: CircleDot,
                        foot: "需求、缺陷与待办",
                        color: "purple",
                      },
                      {
                        title: "快照内待审 PR",
                        value: snapshot.pulls.length,
                        icon: GitPullRequest,
                        foot: "变更与当前提交检查",
                        color: "blue",
                      },
                      {
                        title: "快照内失败 CI",
                        value: failures,
                        icon: TriangleAlert,
                        foot: `${snapshot.runs.length} 次已同步运行`,
                        color: "orange",
                      },
                      {
                        title: "已完成分析",
                        value: runs.filter((r) => r.status === "completed")
                          .length,
                        icon: Bot,
                        foot: "最近 30 条运行记录",
                        color: "green",
                      },
                    ].map((m) => (
                      <div className="metric-card" key={m.title}>
                        <div className="metric-label">
                          {m.title}
                          <span className={`metric-icon ${m.color}`}>
                            <m.icon size={18} />
                          </span>
                        </div>
                        <div className="metric-value">
                          {m.value.toString().padStart(2, "0")}
                          <span>
                            {m.title === "快照内失败 CI" && failures > 0
                              ? "需要关注"
                              : ""}
                          </span>
                        </div>
                        <p>{m.foot}</p>
                      </div>
                    ))}
                  </section>

                  <div className="overview-bottom">
                    <section className="panel repo-panel">
                      <div className="panel-heading">
                        <h3>
                          待办与代码变更{" "}
                          <span className="muted-count">
                            {snapshot.issues.length + snapshot.pulls.length}
                          </span>
                        </h3>
                        <span className="small-muted">
                          {snapshot.branch} 分支
                        </span>
                      </div>
                      <div className="list-tabs">
                        {(
                          [
                            { key: "issues", label: "Issues", icon: CircleDot },
                            {
                              key: "pulls",
                              label: "Pull requests",
                              icon: GitPullRequest,
                            },
                            {
                              key: "runs",
                              label: "CI runs",
                              icon: SquareTerminal,
                            },
                          ] as const
                        ).map((tab) => (
                          <button
                            key={tab.key}
                            className={listTab === tab.key ? "selected" : ""}
                            onClick={() => setListTab(tab.key)}
                          >
                            <tab.icon size={15} />
                            {tab.label}
                            <span>{snapshot[tab.key].length}</span>
                          </button>
                        ))}
                      </div>
                      <div className="repo-list">
                        {listTab === "issues" &&
                          snapshot.issues.map((x) => (
                            <div className="repo-row" key={x.number}>
                              <CircleDot size={18} className="text-purple" />
                              <div className="repo-row-body">
                                <strong>{x.title}</strong>
                                <div>
                                  <span>#{x.number}</span>
                                  {x.labels.map((label) => (
                                    <span
                                      key={label}
                                      className={`tag ${label === "security" ? "tag-red" : ""}`}
                                    >
                                      {label}
                                    </span>
                                  ))}
                                </div>
                              </div>
                              <button
                                className="row-action"
                                disabled={busy}
                                onClick={() =>
                                  analyze(
                                    "issue",
                                    `分诊 Issue #${x.number}：${x.title}`,
                                    x.number,
                                  )
                                }
                              >
                                分析 <ArrowRight size={14} />
                              </button>
                            </div>
                          ))}
                        {listTab === "pulls" &&
                          snapshot.pulls.map((x) => (
                            <div className="repo-row" key={x.number}>
                              <GitPullRequest size={18} className="text-blue" />
                              <div className="repo-row-body">
                                <strong>{x.title}</strong>
                                <div>
                                  <span>#{x.number}</span>
                                  <code>{x.head_sha.slice(0, 7)}</code>
                                </div>
                              </div>
                              <button
                                className="row-action"
                                disabled={busy}
                                onClick={() =>
                                  analyze(
                                    "pr",
                                    `审查 PR #${x.number}：${x.title}`,
                                    x.number,
                                  )
                                }
                              >
                                审查 <ArrowRight size={14} />
                              </button>
                            </div>
                          ))}
                        {listTab === "runs" &&
                          snapshot.runs.map((x: CI) => (
                            <div className="repo-row" key={x.id}>
                              {x.conclusion === "success" ? (
                                <CircleCheck size={18} className="text-green" />
                              ) : (
                                <TriangleAlert
                                  size={18}
                                  className="text-orange"
                                />
                              )}
                              <div className="repo-row-body">
                                <strong>{x.name}</strong>
                                <div>
                                  <span>#{x.id}</span>
                                  <span className="tag">
                                    {x.conclusion || x.status}
                                  </span>
                                  <code>{x.head_sha.slice(0, 7)}</code>
                                </div>
                              </div>
                              <button
                                className="row-action"
                                disabled={busy}
                                onClick={() =>
                                  analyze(
                                    "ci",
                                    `排查 CI run #${x.id}：${x.name}`,
                                    x.id,
                                  )
                                }
                              >
                                排查 <ArrowRight size={14} />
                              </button>
                            </div>
                          ))}
                        {!snapshot[listTab].length && (
                          <div className="empty-small">
                            当前快照没有相关记录
                          </div>
                        )}
                      </div>
                      <div className="panel-bottom">
                        <ShieldCheck size={14} />
                        <span>仓库工具仅执行读取，评论先保存为草稿。</span>
                      </div>
                    </section>

                    <section className="panel quick-panel">
                      <div className="panel-heading">
                        <h3>从一个问题开始</h3>
                        <MessageSquare size={18} />
                      </div>
                      <p className="quick-intro">
                        不用翻遍所有页面，直接说出你要检查的内容。
                      </p>
                      {[
                        {
                          task: "pr" as const,
                          label: "这个 PR 可以合入吗？",
                          desc: "检查变更、风险与当前 CI",
                          icon: GitPullRequest,
                        },
                        {
                          task: "ci" as const,
                          label: "为什么 CI 没有通过？",
                          desc: "提取关键错误与修复方向",
                          icon: SquareTerminal,
                        },
                        {
                          task: "knowledge" as const,
                          label: "项目有哪些发布约定？",
                          desc: "检索文档与原始依据",
                          icon: BookOpen,
                        },
                      ].map((x) => (
                        <button
                          className="quick-question"
                          key={x.task}
                          onClick={() => analyze(x.task, x.label)}
                          disabled={busy}
                        >
                          <span>
                            <x.icon size={17} />
                          </span>
                          <div>
                            <strong>{x.label}</strong>
                            <small>{x.desc}</small>
                          </div>
                          <ArrowUpRight size={16} />
                        </button>
                      ))}
                      <button className="quick-custom" onClick={resetChat}>
                        提出你的问题 <ArrowRight size={15} />
                      </button>
                    </section>
                  </div>
                  <div className="snapshot-note">
                    <span>
                      <span className="status-dot" /> 最后同步{" "}
                      {date(snapshot.synced_at)}
                    </span>
                    <span>{snapshot.coverage}</span>
                  </div>
                </>
              )}

              {page === "workspace" && (
                <div className="workbench-grid">
                  <div className="workbench-main">
                    {runId && ["queued", "running"].includes(runStatus) && (
                      <section className="panel background-panel">
                        <div>
                          <h3>{runStatus === "queued" ? "任务已进入后台队列" : "分析正在后台运行"}</h3>
                          <p>{reconnecting ? "连接中断，正在按已保存进度重连…" : stopping ? "已请求停止，等待后台确认…" : queueStatus?.workers_online === 0 ? "后台执行器尚未连接，任务已保存，等待连接恢复。" : "关闭页面后仍会继续，可从运行记录重新查看进度。"}</p>
                        </div>
                        <button className="button secondary" onClick={leaveAnalysis}>暂离工作台</button>
                      </section>
                    )}
                    <RecoveryPanel
                      recovery={recovery}
                      status={runStatus}
                      busy={busy}
                      onResume={resumeRun}
                    />
                    <WorkflowPlan
                      events={events}
                      result={result}
                      recovery={recovery}
                      active={runStatus === "running"}
                    />
                    <section className="panel chat-panel">
                      <div className="panel-heading">
                        <h3>
                          <Bot size={18} />
                          研发协作助手
                        </h3>
                        <span className="tag">
                          {demo
                            ? "演示规则分析"
                            : result?.workflow?.synthesis_provider ===
                                "deterministic-fallback"
                              ? "汇总降级"
                              : "模型分析"}
                        </span>
                      </div>
                      {!submitted ? (
                        <div className="chat-welcome">
                          <span className="welcome-icon">
                            <Bot size={30} />
                          </span>
                          <h2>今天，想检查什么？</h2>
                          <p>我会读取仓库资料、整理风险，并保留判断依据。</p>
                          <div className="starter-grid">
                            {[
                              {
                                task: "workflow" as const,
                                text: "检查这个版本是否可以发布",
                                icon: Layers3,
                              },
                              {
                                task: "issue" as const,
                                text: "分析优先处理的 Issue",
                                icon: CircleDot,
                              },
                              {
                                task: "pr" as const,
                                text: "审查当前 PR 的合入风险",
                                icon: GitPullRequest,
                              },
                              {
                                task: "ci" as const,
                                text: "定位失败 CI 的根因",
                                icon: SquareTerminal,
                              },
                            ].map((x) => (
                              <button
                                key={x.task}
                                onClick={() => analyze(x.task, x.text)}
                                disabled={busy}
                              >
                                <x.icon size={18} />
                                <span>{x.text}</span>
                                <ArrowUpRight size={14} />
                              </button>
                            ))}
                          </div>
                        </div>
                      ) : (
                        <div className="conversation">
                          <div className="user-message">
                            <span className="avatar">D</span>
                            <p>{submitted}</p>
                          </div>
                          {busy && (
                            <div className="pending-answer">
                              <LoaderCircle className="spin" size={18} />
                              <span>{delta || (runStatus === "queued" ? "任务已保存，正在等待后台执行…" : "正在调用工具、收集证据…")}</span>
                            </div>
                          )}
                          {result && (
                            <div className="answer">
                              <div className="answer-meta">
                                <span className="answer-logo">
                                  <Bot size={17} />
                                </span>
                                <strong>DevFlow AI</strong>
                                <span>
                                  {result.provider === "demo-rules"
                                    ? "演示数据 · 规则分析"
                                    : result.provider === "llm-fallback"
                                      ? "汇总降级 · 保留专用结果"
                                      : "基于工具证据"}
                                </span>
                              </div>
                              <FactReviewPanel key={runId} analysis={result} repositoryId={repositoryId} runId={runId} onReview={setResult}/>
                              <div
                                className={`decision ${result.findings.some((x) => x.severity === "high") || result.fact_review?.status === "conflict" ? "danger" : "neutral"}`}
                              >
                                <ShieldCheck size={20} />
                                <div>
                                  <span>分析建议</span>
                                  <strong>{result.recommendation}</strong>
                                </div>
                                <span className="decision-mode">
                                  人工最终确认
                                </span>
                              </div>
                              <h2>{result.title}</h2>
                              <p className="answer-summary">{result.summary}</p>
                              {result.task_skill&&<details className="task-skill-preview task-skill-result"><summary>任务流程：{result.task_skill.name} · v{result.task_skill.version}</summary>
                                <p className="small-muted">定义 {result.task_skill.definition_hash.slice(0,12)} · {result.task_skill.mode==="demo-rules"?"演示规则分析":"真实模式分析"} · 以下为流程要求，完成情况以回答与证据为准。</p>
                                <ul>{result.task_skill.checklist.map(item=><li key={item}>{item}</li>)}</ul>
                              </details>}
                              {result.findings.length > 0 && (
                                <div className="findings">
                                  {result.findings.map((f, index) => (
                                    <article className="finding" key={index}>
                                      <div>
                                        <span
                                          className={`severity ${f.severity}`}
                                        >
                                          {severityLabels[f.severity]}
                                        </span>
                                        <strong>{f.title}</strong>
                                      </div>
                                      <p>{f.detail}</p>
                                      <div className="evidence-tags">
                                        {f.evidence_ids.map((id) => (
                                          <a
                                            key={id}
                                            href={`#evidence-${id.replace(/:/g, "-")}`}
                                          >
                                            <FileText size={12} />
                                            {result.evidence.find(
                                              (e) => e.id === id,
                                            )?.citation || id}
                                          </a>
                                        ))}
                                      </div>
                                    </article>
                                  ))}
                                </div>
                              )}
                              {result.next_steps.length > 0 && (
                                <div className="next-steps">
                                  <h4>
                                    <CheckCheck size={17} />
                                    建议下一步
                                  </h4>
                                  {result.next_steps.map((step, i) => (
                                    <p key={i}>
                                      <span>{i + 1}</span>
                                      {step}
                                    </p>
                                  ))}
                                </div>
                              )}
                              {result.gaps.length > 0 && (
                                <details className="gaps" open>
                                  <summary>
                                    <TriangleAlert size={15} />
                                    证据范围与待确认项{" "}
                                    <span>{result.gaps.length}</span>
                                  </summary>
                                  <ul>
                                    {result.gaps.map((gap, i) => (
                                      <li key={i}>{gap}</li>
                                    ))}
                                  </ul>
                                </details>
                              )}
                              <div className="answer-actions">
                                <button onClick={createDraft} disabled={result.fact_review?.status === "conflict"} title={result.fact_review?.status === "conflict" ? "先复查源码事实冲突" : undefined}>
                                  <FileText size={14} />
                                  保存评论草稿
                                </button>
                                <button
                                  onClick={() =>
                                    copy(
                                      `${result.title}\n${result.summary}\n建议：${result.recommendation}\n` +
                                        result.findings
                                          .map((x) => `${x.title}：${x.detail}`)
                                          .join("\n"),
                                    )
                                  }
                                >
                                  <Copy size={14} />
                                  复制结果
                                </button>
                              </div>
                            </div>
                          )}
                          {!busy && !result && (
                            <div className="empty-small">
                              {["queued", "running"].includes(runStatus)
                                ? "任务已保留在后台，可从运行记录重新查看进度。"
                                : recovery?.available
                                ? "这次分析未完成。已保存任务结果，可点击上方“从断点继续”。"
                                : "这次分析未完成。可以查看执行轨迹，或重新发起问题。"}
                            </div>
                          )}
                        </div>
                      )}
                      <form
                        className="composer"
                        onSubmit={(e) => {
                          e.preventDefault();
                          analyze(task, question, selectedSkill?.requires_target?Number(skillTarget):undefined, selectedSkill);
                        }}
                      >
                        <textarea
                          aria-label="分析问题"
                          placeholder={
                            demo
                              ? "例如：检查 PR #18 的权限风险，以及对应的 CI 结果…"
                              : "例如：如何启动这个项目？请从文档中检索依据…"
                          }
                          value={question}
                          onChange={(e) => setQuestion(e.target.value)}
                          disabled={busy}
                          maxLength={6000}
                          rows={3}
                        />
                        {repositoryId&&<TaskSkillSelect key={repositoryId} repositoryId={repositoryId} value={selectedSkill} disabled={busy}
                          onChange={skill=>{setSelectedSkill(skill);setSkillTarget("");if(skill)setTask(skill.task as Task);}}/>}
                        {selectedSkill&&<TaskSkillPreview skill={selectedSkill} demo={demo}/>}
                        {selectedSkill?.requires_target&&<label className="task-skill-target">{selectedSkill.task==="pr"?"PR 编号":"CI run ID"}
                          <input aria-label={selectedSkill.task==="pr"?"Skill PR 编号":"Skill CI run ID"} type="number" min="1" step="1" required disabled={busy} value={skillTarget} onChange={e=>setSkillTarget(e.target.value)} placeholder="填写明确的目标编号"/>
                        </label>}
                        <div className="composer-toolbar">
                          <select
                            aria-label="选择分析任务"
                            value={task}
                            onChange={(e) => {setTask(e.target.value as Task);setSelectedSkill(null);setSkillTarget("");}}
                            disabled={busy}
                          >
                            {Object.entries(taskLabels).map(([id, title]) => (
                              <option key={id} value={id}>
                                {title}
                              </option>
                            ))}
                          </select>
                          {repositoryId&&<AnalysisPromptSelect repositoryId={repositoryId} value={promptId} onChange={setPromptId} disabled={busy} demo={demo}/>}
                          <span>
                            <GitBranch size={13} />
                            {snapshot?.name.split("/")[1] || "未连接"}
                          </span>
                          {busy ? (
                            <button
                              type="button"
                              className="stop-button"
                              onClick={stopAnalysis}
                              disabled={stopping}
                            >
                              {stopping ? "正在停止…" : "停止"}
                            </button>
                          ) : (
                            <button
                              type="submit"
                              className="send-button"
                              disabled={!question.trim() || !ready || !mobileApp.online}
                              aria-label="发送问题"
                            >
                              <Send size={16} />
                            </button>
                          )}
                        </div>
                      </form>
                      <div className="composer-note">
                        <ShieldCheck size={12} />
                        {demo
                          ? "演示模式使用样例和规则引擎，未调用大模型。"
                          : "外部数据按不可信内容处理，工具仅执行读取。"}
                      </div>
                    </section>
                    {result && result.evidence.length > 0 && (
                      <section className="panel evidence-panel">
                        <div className="panel-heading">
                          <h3>
                            <BookOpen size={17} />
                            分析依据{" "}
                            <span className="muted-count">
                              {result.evidence.length}
                            </span>
                          </h3>
                          <span className="small-muted">可追溯来源</span>
                        </div>
                        {result.evidence.map((evidence) => (
                          <details
                            id={`evidence-${evidence.id.replace(/:/g, "-")}`}
                            className="evidence-item"
                            key={evidence.id}
                          >
                            <summary>
                              <FileText size={16} />
                              <span>{evidence.title}</span>
                              <code>
                                {evidence.sha?.slice(0, 7) || evidence.id}
                              </code>
                              <ChevronDown size={14} />
                            </summary>
                            <div>
                              <p className="small-muted">
                                {evidence.citation}
                                {evidence.citation ? " · " : ""}
                                {evidence.source === "manual"
                                  ? "本地导入文档"
                                  : evidence.source === "approved_memory"
                                    ? "人工批准的项目记忆"
                                  : evidence.source === "local_project"
                                    ? "本地项目快照"
                                    : evidence.source === "demo"
                                      ? "演示数据"
                                      : "GitHub"}{" "}
                                {evidence.methods?.join(" + ")}
                              </p>
                              <pre>{evidence.content}</pre>
                              {evidence.url && (
                                <a
                                  className="source-link"
                                  href={evidence.url}
                                  target="_blank"
                                  rel="noreferrer"
                                >
                                  查看原始来源 <ExternalLink size={13} />
                                </a>
                              )}
                            </div>
                          </details>
                        ))}
                      </section>
                    )}
                  </div>
                  <aside className="workbench-aside">
                    <section className="panel trace-panel">
                      <div className="panel-heading">
                        <h3>执行轨迹</h3>
                        <span
                          className={
                            busy ? "trace-status running" : "trace-status"
                          }
                        >
                          {runStatus === "queued"
                            ? "QUEUED"
                            : runStatus === "running"
                              ? "RUNNING"
                            : result
                              ? "COMPLETED"
                            : events.length
                                ? "STOPPED"
                                : "READY"}
                        </span>
                      </div>
                      {visibleEvents.length ? (
                        <div className="trace-list">
                          {visibleEvents.map((event) => (
                            <div
                              className={`trace-item ${event.type.includes("failed") ? "failed" : ""}`}
                              key={event.sequence}
                            >
                              <span className="trace-bullet">
                                {event.type.includes("failed") ? (
                                  <X size={11} />
                                ) : (
                                  <Check size={11} />
                                )}
                              </span>
                              <div>
                                <strong>{eventLabel(event)}</strong>
                                <small>
                                  步骤{" "}
                                  {event.sequence.toString().padStart(2, "0")}
                                </small>
                              </div>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className="trace-empty">
                          <Activity size={27} />
                          <p>
                            分析开始后，每一步
                            <br />
                            工具调用都会显示在这里。
                          </p>
                        </div>
                      )}
                    </section>
                    <section className="context-card">
                      <div>
                        <GitBranch size={17} />
                        <h4>当前上下文</h4>
                      </div>
                      <p>
                        仓库 <strong>{snapshot?.name}</strong>
                      </p>
                      <p>
                        分支 <code>{snapshot?.branch}</code>
                      </p>
                      <p>
                        数据来源{" "}
                        <strong>{demo ? "自带演示数据" : "GitHub 快照"}</strong>
                      </p>
                      <p>
                        检索方式 <strong>{health?.retrieval || "BM25"}</strong>
                      </p>
                      <p>
                        会话{" "}
                        <code>{conversationId?.slice(0, 8) || "尚未创建"}</code>
                      </p>
                      <div className="context-note">
                        协作检查按问题生成任务计划；只覆盖列出的目标，失败与补查均保留记录。
                      </div>
                    </section>
                  </aside>
                </div>
              )}

              {page === "code" && repositoryId && (
                <CodePanel
                  key={repositoryId}
                  repositoryId={repositoryId}
                  emptyRemote={!!snapshot?.sync_info?.empty_repository}
                  busy={busy}
                  onAsk={(text) => analyze("workflow", text)}
                />
              )}

              {page === "knowledge" && repositoryId && (
                <KnowledgePanel
                  key={repositoryId}
                  repositoryId={repositoryId}
                  busy={busy}
                  onAsk={(question) => analyze("knowledge", question)}
                />
              )}

              {page === "history" && (
                <>
                  <section className="panel history-panel">
                    <div className="panel-heading">
                      <h3>
                        最近运行{" "}
                        <span className="muted-count">{runs.length}</span>
                      </h3>
                      <button
                        className="text-button"
                        onClick={() =>
                          reloadRuns().catch((e) => setError(e.message))
                        }
                      >
                        <RefreshCw size={14} />
                        刷新
                      </button>
                    </div>
                    {runs.length ? (
                      <div className="history-table">
                        <div className="history-row table-labels">
                          <span>任务与问题</span>
                          <span>状态</span>
                          <span>创建时间</span>
                          <span />
                        </div>
                        {runs.map((run) => (
                          <button
                            key={run.id}
                            className="history-row"
                            onClick={() => openRun(run)}
                            disabled={busy}
                          >
                            <div>
                              <span className="tag">
                                {taskLabels[run.task]}
                              </span>
                              <strong>{run.question}</strong>
                              <code>{run.id.slice(0, 10)}</code>
                            </div>
                            <span className={`run-state ${run.status}`}>
                              {(
                                {
                                  completed: "已完成",
                                  failed: "失败",
                                  running: "运行中",
                                  queued: "排队中",
                                  cancelled: "已停止",
                                  interrupted: "被中断",
                                } as Record<string, string>
                              )[run.status] || run.status}
                            </span>
                            <span className="small-muted">
                              {date(run.created_at)}
                            </span>
                            <ArrowUpRight size={16} />
                          </button>
                        ))}
                      </div>
                    ) : (
                      <div className="empty-large">
                        <History size={30} />
                        <h2>还没有分析记录</h2>
                        <p>发起一次分析后，结果和执行轨迹会保存在这里。</p>
                        <button className="button primary" onClick={resetChat}>
                          发起第一次分析 <ArrowRight size={15} />
                        </button>
                      </div>
                    )}
                  </section>
                  <section className="panel drafts-panel">
                    <div className="panel-heading">
                      <h3>
                        <FileText size={17} />
                        评论草稿{" "}
                        <span className="muted-count">{drafts.length}</span>
                      </h3>
                      <button className="text-button" onClick={()=>setPage("governance")}>打开草稿审批</button>
                    </div>
                    {drafts.length ? (
                      drafts.map((draft) => (
                        <details className="draft-item" key={draft.id}>
                          <summary>
                            <span>草稿 {draft.id.slice(0, 8)}</span>
                            <span className="small-muted">
                              {date(draft.created_at)}
                            </span>
                            <ChevronDown size={14} />
                          </summary>
                          <pre>{draft.body}</pre>
                          <button
                            className="text-button"
                            onClick={() => copy(draft.body)}
                          >
                            <Copy size={14} />
                            复制草稿
                          </button>
                        </details>
                      ))
                    ) : (
                      <p className="empty-small">
                        分析完成后，点击“保存评论草稿”即可创建，再到“记忆与审批”提交审核。
                      </p>
                    )}
                  </section>
                </>
              )}

              {page === "governance" && repositoryId && <GovernancePanel key={repositoryId} repositoryId={repositoryId} repoName={snapshot?.name||""} auth={authState} sourceRun={runId&&result?{id:runId,result}:null}/>}
              {page === "delivery" && repositoryId && <DeliveryPanel key={repositoryId} repositoryId={repositoryId}/>}
              {page === "settings" && (
                <div className="settings-grid">
                  <MobileInstallPanel/>
                  {repositoryId&&<SyncPanel key={repositoryId} repositoryId={repositoryId} syncing={syncing} onSync={sync} onReload={async()=>{setSnapshot(await api<Snapshot>(`/repositories/${repositoryId}/snapshot`));}}/>}
                  <section className="panel settings-panel">
                    <div className="panel-heading">
                      <h3>运行配置</h3>
                      <Settings2 size={18} />
                    </div>
                    <div className="settings-rows">
                      {[
                        {
                          label: "运行模式",
                          value: demo ? "Demo · 演示样例" : "Live · 真实连接",
                        },
                        { label: "模型", value: health?.model || "尚未连接" },
                        {
                          label: "数据库",
                          value:
                            health?.storage === "sqlite"
                              ? "SQLite · 本地开发"
                              : health?.storage || "尚未连接",
                        },
                        {
                          label: "知识检索",
                          value: health?.retrieval || "尚未连接",
                        },
                        {
                          label: "外部写操作",
                          value: health?.external_writes ? "人工审批后发布" : "发布流程已实现 · 写回未启用",
                        },
                      ].map((x) => (
                        <div key={x.label}>
                          <span>{x.label}</span>
                          <strong>{x.value}</strong>
                        </div>
                      ))}
                    </div>
                    <div className="config-instructions">
                      <h4>切换真实模式</h4>
                      <p>
                        在 <code>backend/.env</code>{" "}
                        设置下列字段，然后重启后端和 Worker。密钥只存放在本地文件。
                      </p>
                      <pre>
                        {
                          "DEVFLOW_MODE=live\nGITHUB_TOKEN=你的只读 Token\nLLM_BASE_URL=模型服务的 /v1 地址\nLLM_API_KEY=你的模型密钥\nLLM_MODEL=支持 Tool Calling 的模型"
                        }
                      </pre>
                      <p>
                        部署时将 <code>DATABASE_URL</code> 改为
                        PostgreSQL。Docker Compose 的数据库配置已准备。
                      </p>
                    </div>
                  </section>
                  <section className="panel settings-panel">
                    <div className="panel-heading">
                      <h3>仓库连接</h3>
                      <GitBranch size={18} />
                    </div>
                    {repositories.map((r) => (
                      <div className="connected-repo" key={r.id}>
                        <GitBranch size={18} />
                        <div>
                          <strong>{r.full_name}</strong>
                          <small>
                            {r.source === "demo" ? "自带演示仓库" : "GitHub"}
                          </small>
                        </div>
                        <span className="tag">已连接</span>
                      </div>
                    ))}
                    <button
                      className="button secondary full-width"
                      onClick={() => {
                        setShowAdd(true);
                        setError("");
                      }}
                      disabled={demo || busy || syncing}
                    >
                      <Plus size={16} />
                      添加 GitHub 仓库
                    </button>
                    {demo && (
                      <p className="setting-note">
                        当前是演示模式，切换 live 并重启后可添加真实仓库。
                      </p>
                    )}
                    <div className="implementation-list">
                      <h4>实现路线与验证状态</h4>
                      {[
                        "基础链路：已验证 · 看板、工具、记录",
                        "数据与检索：本地向量已联调 · Webhook 本地已测",
                        "复杂协作：已验证 · 规划、后台、恢复",
                        "记忆审批：已实现 · 记忆、权限、审批",
                        "评测交付：当前阶段 · 评测、用量、周报、MCP",
                      ].map((x, i) => (
                        <div key={x}>
                          <span className={i === 4 ? "current" : ""}>
                            {i + 1}
                          </span>
                          <p>{x}</p>
                          {[0,2].includes(i) && <Check size={15} />}
                        </div>
                      ))}
                    </div>
                  </section>
                </div>
              )}
            </>
          )}
          <footer className="page-footer">
            <span>
              DevFlow AI <span>·</span> 以证据推进研发
            </span>
            <span>
              <Command size={12} />
              Python + Next.js <span>·</span> 本地开发版
            </span>
          </footer>
        </div>
      </main>
      <MobileNavigation page={page} onNavigate={next=>next==="workspace"?returnToWorkspace():setPage(next)} repositories={repositories}
        repositoryId={repositoryId} onRepositoryChange={setRepositoryId} disabled={busy||loading}/>
      {toast && (
        <div className="toast" role="status">
          <CircleCheck size={18} />
          {toast}
        </div>
      )}
      {showAdd && (
        <div className="modal-overlay">
          <form
            className="modal"
            onSubmit={(e) => {
              e.preventDefault();
              addRepo();
            }}
          >
            <div>
              <h2>连接 GitHub 仓库</h2>
              <button
                type="button"
                className="icon-button"
                onClick={() => setShowAdd(false)}
                aria-label="关闭"
              >
                <X size={20} />
              </button>
            </div>
            <p>输入 owner/repository，例如你的用户名和仓库名。</p>
            <input
              aria-label="GitHub 仓库名称"
              required
              value={newRepo}
              onChange={(e) => setNewRepo(e.target.value)}
              placeholder="owner/repository"
              pattern="[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
            />
            {error && <p className="text-orange">{error}</p>}
            <button className="button primary" disabled={syncing}>
              {syncing ? (
                <LoaderCircle className="spin" size={16} />
              ) : (
                <GitBranch size={16} />
              )}
              {syncing ? "同步中" : "连接并同步"}
            </button>
          </form>
        </div>
      )}
    </div>
  );
}
