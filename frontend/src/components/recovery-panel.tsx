"use client";
import { RotateCcw, LoaderCircle, BookmarkCheck } from "lucide-react";
import { RunRecovery } from "@/lib/api";

export function RecoveryPanel({
  recovery,
  status,
  busy,
  onResume,
}: {
  recovery: RunRecovery | null;
  status: string;
  busy: boolean;
  onResume: () => void;
}) {
  if (!recovery || status === "completed" || status === "running" || status === "queued") return null;
  return (
    <section className="panel recovery-panel">
      <span className="recovery-icon">
        <BookmarkCheck size={23} />
      </span>
      <div>
        <h3>
          运行
          {status === "cancelled"
            ? "已停止"
            : status === "interrupted"
              ? "被中断"
              : "未完成"}
          ，恢复点已保存
        </h3>
        <p>
          保留 {recovery.settled_tasks} / {recovery.total_tasks || "待规划"}{" "}
          项任务结果 · 已恢复 {recovery.resume_count} 次
        </p>
        <small>{recovery.reason}</small>
      </div>
      {recovery.available && (
        <button className="button primary" disabled={busy} onClick={onResume}>
          {busy ? (
            <LoaderCircle size={15} className="spin" />
          ) : (
            <RotateCcw size={15} />
          )}{" "}
          从断点继续
        </button>
      )}
    </section>
  );
}
