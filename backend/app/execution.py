"""Stream run events and serialize checkpoints and final status in the same database."""
import asyncio
import contextlib
import copy
import json
from time import perf_counter

from fastapi.responses import StreamingResponse
from langgraph.errors import NodeCancelledError
from sqlalchemy import update

from .agents import AgentService, safe_error
from .checkpoints import CheckpointError, CheckpointStore
from .db import AgentRun, RunCheckpoint, RunJob, SessionLocal, utcnow
from .tools import Tools
from .telemetry import attempt_context, start_attempt, end_attempt
from .prompts import bound_settings

ACTIVE_RUNS: dict[str, asyncio.Task] = {}


def stream_run(run_id, settings, token=None, resumed=False, snapshot=None, target=None, history=None, frozen_input=None, job_lease=None):
    checkpoint = CheckpointStore(run_id, token, job_lease=job_lease) if token else None
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        repository_id, conversation_id = run.repository_id, run.conversation_id
        question, task = run.question, run.task
        events = list(run.events or [])
    frozen = checkpoint.state["input"] if checkpoint else frozen_input
    if frozen:
        snapshot, target, history = frozen["snapshot"], frozen["target"], frozen["history"]
    queue, event_lock = asyncio.Queue(), asyncio.Lock()

    async def emit(kind, data):
        async with event_lock:
            event = {"type": kind, "data": data, "sequence": len(events) + 1}
            with SessionLocal() as db:
                if job_lease:
                    owned = db.execute(update(RunJob).where(RunJob.run_id == run_id, RunJob.status == "running", RunJob.lease_token == job_lease, RunJob.lease_until > utcnow()).values(updated_at=utcnow()))
                    if owned.rowcount != 1: raise CheckpointError("后台任务的执行权已失效。")
                saved = db.get(AgentRun, run_id)
                cp = db.get(RunCheckpoint, run_id) if token else None
                if token and (not cp or cp.lease_token != token):
                    raise CheckpointError("该运行的执行权已变化，已停止旧执行任务。")
                if kind == "run.completed":
                    saved.status, saved.result = "completed", data["result"]
                    if cp: cp.state = {**cp.state, "phase": "done"}
                elif kind == "run.failed": saved.status = "failed"
                elif kind == "run.cancelled" and saved.status == "running": saved.status = "cancelled"
                if job_lease and kind in ("run.completed", "run.failed", "run.cancelled"):
                    job = db.get(RunJob, run_id)
                    job.status, job.lease_until = saved.status, None
                saved.events = events + [event]
                db.commit()
            events.append(event)
            await queue.put(event)

    async def execute():
        tools = None
        attempt, started, status = start_attempt(run_id), perf_counter(), "failed"
        context_token = attempt_context.set(attempt)
        try:
            run_settings = bound_settings(settings, frozen)
            kwargs = {"workspace_ref": frozen["workspace"], "knowledge_corpus": frozen["corpus"], "memory_corpus":frozen.get("memories",[])} if frozen else {}
            tools = Tools(run_settings, snapshot, emit, repository_id, **kwargs)
            await emit("run.resumed" if resumed else "run.started", {"run_id": run_id, "conversation_id": conversation_id,
                       "mode": settings.devflow_mode, "resume_count": checkpoint.resume_count if checkpoint else 0,
                       "prompt_id": run_settings.analysis_prompt_id if settings.devflow_mode == "live" else None,
                       "skill_id": run_settings.analysis_skill_id,
                       "retained_tasks": len(checkpoint.state["outcomes"]) if checkpoint else 0})
            if checkpoint and checkpoint.state.get("answer"):
                answer = copy.deepcopy(checkpoint.state["answer"])
                answer["workflow"]["resume_count"] = checkpoint.resume_count
                await emit("answer.reused", {"message": "汇总结果已保存，直接恢复输出，没有重复调用模型。"})
            else:
                answer = await AgentService(tools, question, history, checkpoint=checkpoint).run(task, target)
            if checkpoint:
                await checkpoint.update(phase="ready", answer=answer)
            for offset in range(0, len(answer["summary"]), 28):
                await emit("answer.delta", {"text": answer["summary"][offset:offset + 28]})
            await emit("run.completed", {"run_id": run_id, "result": answer})
            status = "completed"
        except (asyncio.CancelledError, NodeCancelledError):
            status = "cancelled"
            await emit("run.cancelled", {"run_id": run_id, "message": "运行已停止，已持久化的任务结果与事件保留。"})
            raise asyncio.CancelledError() from None
        except Exception as exc:
            await emit("run.failed", {"run_id": run_id, "message": safe_error(exc)})
        finally:
            try:
                if tools:
                    with contextlib.suppress(Exception): await tools.close()
            finally:
                try:
                    end_attempt(attempt, status, int((perf_counter()-started)*1000))
                finally:
                    attempt_context.reset(context_token)
                    if ACTIVE_RUNS.get(run_id) is asyncio.current_task(): ACTIVE_RUNS.pop(run_id, None)
                    await queue.put(None)

    async def stream():
        worker = asyncio.create_task(execute())
        ACTIVE_RUNS[run_id] = worker
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if event is None: break
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        finally:
            if not worker.done(): worker.cancel()
            with contextlib.suppress(asyncio.CancelledError): await worker
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
