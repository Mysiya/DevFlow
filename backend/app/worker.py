"""Run with python -m app.worker; execution is independent of the API and browser."""
import asyncio
import contextlib
import signal
from sqlalchemy import update

from .agents import safe_error
from .checkpoints import CheckpointError, model_key, validate_checkpoint
from .config import get_settings
from .db import AgentRun, Base, RunCheckpoint, RunJob, QueueWorker, SessionLocal, engine, new_id, utcnow
from .execution import stream_run
from .resumption import validate_sources
from .run_queue import append_event, claim_job, renew_job
from .synchronization import claim_delivery, execute_delivery


def finish_claim(claim, kind, message):
    with SessionLocal() as db:
        conditions = [RunJob.run_id == claim["run_id"], RunJob.lease_token == claim["token"], RunJob.status.in_(("running", "cancelled"))]
        if kind == "interrupted": conditions.append(RunJob.stop_requested.is_(False))
        changed = db.execute(update(RunJob).where(*conditions).values(status=kind, lease_until=None, lease_token=new_id(), updated_at=utcnow()))
        if changed.rowcount != 1: return
        run, cp = db.get(AgentRun, claim["run_id"]), db.get(RunCheckpoint, claim["run_id"])
        run.status = kind
        if cp: cp.lease_token = new_id()
        append_event(run, "run." + kind, {"run_id": run.id, "message": message})
        db.commit()


async def execute_claim(claim, settings):
    try:
        payload = claim["payload"]
        if payload["mode"] != settings.devflow_mode or payload["model_key"] != model_key(settings):
            raise CheckpointError("队列任务的运行模式或模型配置与 Worker 不同，未调用模型。")
        if payload["resumed"]:
            with SessionLocal() as db:
                state = validate_checkpoint(db.get(RunCheckpoint, claim["run_id"]), db.get(AgentRun, claim["run_id"]))
            await validate_sources(state, settings)
        response = stream_run(claim["run_id"], settings, token=claim["checkpoint_token"], resumed=payload["resumed"],
                              frozen_input=payload["input"], job_lease=claim["token"])
        async for _ in response.body_iterator:
            pass
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        finish_claim(claim, "failed", safe_error(exc))


def heartbeat_worker(worker_id, settings, stopped=False):
    with SessionLocal() as db:
        worker = db.get(QueueWorker, worker_id)
        if not worker:
            worker = QueueWorker(id=worker_id, concurrency=settings.worker_concurrency)
            db.add(worker)
        worker.heartbeat_at, worker.stopped = utcnow(), stopped
        db.commit()


async def run_worker(settings=None, stop=None):
    settings, stop = settings or get_settings(), stop or asyncio.Event()
    Base.metadata.create_all(engine)
    worker_id, active, sync_task = new_id(), {}, None
    heartbeat_worker(worker_id, settings)
    print(f"DevFlow Worker ready: concurrency={settings.worker_concurrency}, lease={settings.job_lease_seconds}s", flush=True)
    try:
        while not stop.is_set():
            heartbeat_worker(worker_id, settings)
            if sync_task and sync_task.done():
                with contextlib.suppress(asyncio.CancelledError,Exception):sync_task.result()
                sync_task=None
            if sync_task is None:
                sync_claim=claim_delivery(settings)
                if sync_claim:sync_task=asyncio.create_task(execute_delivery(sync_claim,settings))
            for ident, (claim, task) in list(active.items()):
                if task.done():
                    with contextlib.suppress(asyncio.CancelledError, Exception): task.result()
                    active.pop(ident)
                    continue
                control = renew_job(ident, claim["token"], settings)
                if control != "running" and not task.cancelling(): task.cancel()
            while len(active) < settings.worker_concurrency and not stop.is_set():
                claim = claim_job(settings, worker_id)
                if not claim: break
                active[claim["run_id"]] = (claim, asyncio.create_task(execute_claim(claim, settings)))
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.queue_poll_seconds)
            except asyncio.TimeoutError:
                pass
    finally:
        if sync_task:
            if not sync_task.done():sync_task.cancel()
            with contextlib.suppress(asyncio.CancelledError,Exception):await sync_task
        for claim, task in active.values():
            if not task.done() and not task.cancelling(): task.cancel()
        for claim, task in active.values():
            with contextlib.suppress(asyncio.CancelledError, Exception): await task
            finish_claim(claim, "interrupted", "后台 Worker 已停止，进度保留，请手动恢复。")
        heartbeat_worker(worker_id, settings, stopped=True)


async def main():
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    def shutdown(*_): loop.call_soon_threadsafe(stop.set)
    for sig in (signal.SIGINT, signal.SIGTERM): signal.signal(sig, shutdown)
    await run_worker(stop=stop)


if __name__ == "__main__":
    asyncio.run(main())
