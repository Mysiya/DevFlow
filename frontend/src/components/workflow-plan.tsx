"use client";
import {
  Layers3,
  CircleCheck,
  CircleDot,
  LoaderCircle,
  TriangleAlert,
} from "lucide-react";
import { Analysis, StreamEvent, TaskPlan, RunRecovery } from "@/lib/api";

const labels: Record<string, string> = {
  pending: "等待执行",
  paused: "待恢复",
  running: "执行中",
  completed: "已完成",
  partial: "证据不足",
  failed: "未完成",
  skipped: "已跳过",
};
const agents: Record<string, string> = {
  issue: "Issue 分诊",
  pr: "PR 审查",
  ci: "CI 排障",
  knowledge: "文档检索",
  code: "源码分析",
  report: "快照核对",
};

export function WorkflowPlan({
  events,
  result,
  recovery,
  active,
}: {
  events: StreamEvent[];
  result: Analysis | null;
  recovery?: RunRecovery | null;
  active?: boolean;
}) {
  const plans: TaskPlan[] =
    result?.workflow?.plans ||
    Array.from(
      new Map(
        [
          ...(recovery?.plans || []),
          ...events
            .filter((x) => x.type === "plan")
            .map((x) => ({
              reason: String(x.data.reason || ""),
              steps: x.data.steps as TaskPlan["steps"],
              provider: String(x.data.planner),
              round: Number(x.data.round || 0),
            })),
        ].map((plan) => [plan.round, plan]),
      ).values(),
    );
  if (!plans.length) return null;
  const statuses: Record<string, string> = {};
  for (const event of events) {
    if (event.type === "task.started")
      statuses[String(event.data.id)] = "running";
    if (event.type === "task.completed" || event.type === "task.reused")
      statuses[String(event.data.id)] = String(
        event.data.status || "completed",
      );
    if (event.type === "task.failed")
      statuses[String(event.data.id)] = "failed";
    if (event.type === "task.skipped")
      statuses[String(event.data.id)] = "skipped";
  }
  for (const outcome of result?.workflow?.outcomes || recovery?.outcomes || [])
    statuses[outcome.id] = outcome.status;
  const outcomes = result?.workflow?.outcomes || recovery?.outcomes || [];
  if (!active)
    for (const key of Object.keys(statuses))
      if (statuses[key] === "running") statuses[key] = "paused";
  const workspace = result?.workflow?.workspace || recovery?.workspace;
  const resumeCount =
    result?.workflow?.resume_count || recovery?.resume_count || 0;
  return (
    <section className="panel workflow-plan-panel">
      <div className="panel-heading">
        <h3>
          <Layers3 size={17} />
          任务计划
        </h3>
        <span className="tag">
          {plans[0].provider === "llm"
            ? "模型规划"
            : plans[0].provider === "template-demo"
              ? "演示模板"
              : "模板降级"}
        </span>
      </div>
      {plans.map((plan) => (
        <div className="workflow-plan-round" key={plan.round}>
          <p>
            {plan.round ? "补查计划 · " : ""}
            {plan.reason}
          </p>
          <div className="workflow-task-grid">
            {plan.steps.map((step, i) => {
              const status = statuses[step.id] || "pending";
              const outcome = outcomes.find((x) => x.id === step.id);
              return (
                <article className={`workflow-task ${status}`} key={step.id}>
                  <div>
                    <span className="workflow-step-number">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    <span className="small-muted">
                      {agents[step.agent] || step.agent}
                    </span>
                    <span className="workflow-task-status">
                      {status === "running" ? (
                        <LoaderCircle size={13} className="spin" />
                      ) : status === "completed" ? (
                        <CircleCheck size={13} />
                      ) : status === "failed" || status === "partial" ? (
                        <TriangleAlert size={13} />
                      ) : (
                        <CircleDot size={13} />
                      )}{" "}
                      {labels[status] || status}
                    </span>
                  </div>
                  <h4>
                    {step.title}
                    {step.target ? ` · #${step.target}` : ""}
                  </h4>
                  {step.query && (
                    <p className="workflow-task-query">{step.query}</p>
                  )}
                  <small>
                    {step.depends_on.length
                      ? "等待：" +
                        step.depends_on
                          .map(
                            (x) =>
                              plan.steps.find((s) => s.id === x)?.title || x,
                          )
                          .join("、")
                      : "可独立执行"}
                  </small>
                  {outcome?.gaps.length ? (
                    <details>
                      <summary>查看任务缺口（{outcome.gaps.length}）</summary>
                      {outcome.gaps.map((gap, j) => (
                        <p key={j}>{gap}</p>
                      ))}
                    </details>
                  ) : null}
                </article>
              );
            })}
          </div>
        </div>
      ))}
      {workspace && (
        <div className="workflow-version">
          <span>源码版本</span>
          <code>{workspace.sha.slice(0, 12)}</code>
          <span>
            {workspace.source === "local_project"
              ? "本地项目快照"
              : "GitHub 提交"}
          </span>
        </div>
      )}
      {resumeCount > 0 && (
        <div className="workflow-version">
          <span>已恢复 {resumeCount} 次</span>
          <span>保留已保存的任务结果</span>
        </div>
      )}
      {result?.workflow?.synthesis_provider === "deterministic-fallback" && (
        <p className="knowledge-message">
          模型汇总未完成，当前展示规则汇总和已经完成的专用结果。
        </p>
      )}
    </section>
  );
}
