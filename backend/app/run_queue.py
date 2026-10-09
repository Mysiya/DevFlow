"""Durable SQL queue; claiming and fencing use conditional writes in one transaction."""
import asyncio
import json
from datetime import timedelta

from fastapi.responses import StreamingResponse
from sqlalchemy import select, update, func

from .checkpoints import CheckpointError, bounded_state, model_key
from .db import AgentRun, RunCheckpoint, RunJob, QueueWorker, SessionLocal, new_id, utcnow

ACTIVE_STATUSES = {"queued", "running"}


def enqueue(db, run, settings, frozen, resumed=False):
    if db.scalar(select(func.count()).select_from(RunJob).where(RunJob.status.in_(ACTIVE_STATUSES))) >= 100:
        raise CheckpointError("后台队列已满，请稍后提交。")
    payload = bounded_state({"input": frozen, "resumed": resumed, "mode": run.mode, "model_key": model_key(settings)})
    job = db.get(RunJob, run.id)
    if job and job.status in ACTIVE_STATUSES:
        raise CheckpointError("该运行已经在后台队列中，不能重复提交。")
    if not job:
        job = RunJob(run_id=run.id, payload=payload)
        db.add(job)
    job.payload, job.status, job.stop_requested = payload, "queued", False
    job.worker_id, job.lease_token, job.lease_until = None, None, None
    job.created_at = job.updated_at = utcnow()
    run.status = "queued"
    append_event(run, "run.queued", {"run_id": run.id, "conversation_id": run.conversation_id, "resumed": resumed})
    return job


def append_event(run, kind, data):
    events = run.events or []
    run.events = events + [{"type": kind, "data": data, "sequence": len(events) + 1}]


def expire_jobs(db):
    """Never retry models automatically. Invalidate the previous writer before exposing resume."""
    now = utcnow()
    candidates = list(db.scalars(select(RunJob.run_id).where(RunJob.status == "running", RunJob.lease_until <= now)))
    for ident in candidates:
        changed = db.execute(update(RunJob).where(RunJob.run_id == ident, RunJob.status == "running", RunJob.lease_until <= now).values(status="interrupted", lease_token=new_id(), lease_until=None, updated_at=now))
        if changed.rowcount != 1: continue
        run, cp = db.get(AgentRun, ident), db.get(RunCheckpoint, ident)
        if cp: cp.lease_token = new_id()
        run.status = "interrupted"
        append_event(run, "run.interrupted", {"run_id": ident, "message": "后台 Worker 心跳已过期，已保存的进度保留，请手动恢复。"})
    db.commit()


def claim_job(settings, worker_id):
    with SessionLocal() as db:
        expire_jobs(db)
        candidates = list(db.scalars(select(RunJob.run_id).where(RunJob.status == "queued").order_by(RunJob.created_at).limit(10)))
        for ident in candidates:
            token, now = new_id(), utcnow()
            changed = db.execute(update(RunJob).where(RunJob.run_id == ident, RunJob.status == "queued", RunJob.stop_requested.is_(False)).values(status="running", lease_token=token, worker_id=worker_id, lease_until=now + timedelta(seconds=settings.job_lease_seconds), updated_at=now))
            if changed.rowcount != 1:
                db.rollback()
                continue
            run, cp, job = db.get(AgentRun, ident), db.get(RunCheckpoint, ident), db.get(RunJob, ident)
            if run.status != "queued":
                job.status, job.lease_until = "failed", None
                db.commit()
                continue
            run.status = "running"
            if cp: cp.lease_token = token
            db.commit()
            return {"run_id": ident, "token": token, "checkpoint_token": token if cp else None, "payload": job.payload}
    return None


def renew_job(run_id, token, settings):
    with SessionLocal() as db:
        now = utcnow()
        changed = db.execute(update(RunJob).where(RunJob.run_id == run_id, RunJob.status == "running", RunJob.lease_token == token, RunJob.lease_until > now).values(lease_until=now + timedelta(seconds=settings.job_lease_seconds), updated_at=now))
        if changed.rowcount != 1:
            db.rollback()
            return "lost"
        job = db.get(RunJob, run_id)
        stop = job.stop_requested
        db.commit()
        return "stop" if stop else "running"


def request_stop(db, run):
    job = db.get(RunJob, run.id)
    if not job: return False
    if job.status == "queued":
        changed = db.execute(update(RunJob).where(RunJob.run_id == run.id, RunJob.status == "queued").values(status="cancelled", stop_requested=True, updated_at=utcnow()))
        if changed.rowcount == 1:
            run.status = "cancelled"
            append_event(run, "run.cancelled", {"run_id": run.id, "message": "已取消排队任务，没有调用模型。"})
    # A concurrent claim is covered by the same flag after the conditional update.
    db.execute(update(RunJob).where(RunJob.run_id == run.id, RunJob.status == "running").values(stop_requested=True))
    db.commit()
    return True


def queue_status(db, settings, repository_ids=None):
    expire_jobs(db)
    fresh = utcnow() - timedelta(seconds=settings.job_lease_seconds)
    workers = list(db.scalars(select(QueueWorker).where(QueueWorker.stopped.is_(False), QueueWorker.heartbeat_at > fresh)))
    query = select(RunJob.status, func.count()).join(AgentRun,AgentRun.id==RunJob.run_id)
    if repository_ids is not None: query = query.where(AgentRun.repository_id.in_(repository_ids))
    counts = dict(db.execute(query.group_by(RunJob.status)).all())
    return {"backend": "sql", "workers_online": len(workers), "concurrency": sum(x.concurrency for x in workers),
            "queued": counts.get("queued", 0), "running": counts.get("running", 0), "lease_seconds": settings.job_lease_seconds}


def event_stream(run_id, after, poll_seconds=0.5):
    """A subscriber owns no execution task. Disconnecting only closes this iterator."""
    async def stream():
        cursor, idle = after, 0.0
        while True:
            with SessionLocal() as db:
                expire_jobs(db)
                run = db.get(AgentRun, run_id)
                pending = [x for x in run.events if x["sequence"] > cursor]
                status = run.status
            for event in pending:
                cursor, idle = event["sequence"], 0
                yield f"id: {cursor}\ndata: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            if status not in ACTIVE_STATUSES: break
            idle += poll_seconds
            if idle >= 15:
                yield ": heartbeat\n\n"
                idle = 0
            await asyncio.sleep(poll_seconds)
    # Next.js' compression proxy otherwise buffers short events until its gzip buffer fills.
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control":"no-cache, no-transform", "X-Accel-Buffering":"no"})
