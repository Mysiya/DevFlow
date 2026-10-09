"""Serialize repository refreshes and fence stale writers across API/Worker processes."""
import asyncio
import copy
from datetime import timedelta
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .db import Repository, RepositorySyncState, WebhookDelivery, SessionLocal, new_id, utcnow
from .github import GitHubClient
from .knowledge import sync_snapshot_documents

SCOPES={"repository","issues","pulls","runs","documents"}
LEASE_SECONDS=180


class SyncError(RuntimeError):
    pass


def allowed_ids(settings):
    return {ident.strip() for ident in settings.github_webhook_repository_ids.split(",") if ident.strip()}


def ensure_state(db,repo_id):
    if db.get(RepositorySyncState,repo_id):return
    db.add(RepositorySyncState(repository_id=repo_id))
    try:db.commit()
    except IntegrityError:db.rollback()


def take_lease(db,repo_id):
    token,now=new_id(),utcnow()
    changed=db.execute(update(RepositorySyncState).where(RepositorySyncState.repository_id==repo_id,
        (RepositorySyncState.lease_until.is_(None)) | (RepositorySyncState.lease_until<=now)).values(
        lease_token=token,lease_until=now+timedelta(seconds=LEASE_SECONDS),status="running",message="正在读取 GitHub 当前状态",updated_at=now).execution_options(synchronize_session=False))
    return token if changed.rowcount==1 else None


async def fetch_current(settings,name,previous,scopes):
    github=GitHubClient(settings,previous.get("_http_cache"))
    try:return await github.snapshot(name,previous,scopes)
    finally:await github.close()


def release_claim(repo_id,token,status,message,delivery_id=None,requeue=False):
    with SessionLocal() as db:
        now=utcnow()
        changed=db.execute(update(RepositorySyncState).where(RepositorySyncState.repository_id==repo_id,RepositorySyncState.lease_token==token).values(
            lease_token=None,lease_until=None,status=status,message=message,updated_at=now))
        if changed.rowcount==1 and delivery_id:
            job=db.get(WebhookDelivery,delivery_id)
            if job and job.lease_token==token:
                job.status="queued" if requeue and job.attempts<3 else "failed"
                job.lease_token=job.lease_until=None;job.message=message;job.updated_at=now
        db.commit()


async def refresh_claim(settings,repo_id,token,scopes,delivery_id=None,fetcher=None):
    try:
        with SessionLocal() as db:
            repo=db.get(Repository,repo_id)
            if not repo:raise SyncError("仓库不存在，已停止刷新。")
            previous,name=copy.deepcopy(repo.snapshot),repo.full_name
            if delivery_id:
                job=db.get(WebhookDelivery,delivery_id)
                if not settings.github_webhook_enabled or repo_id not in allowed_ids(settings) or settings.devflow_mode!="live" or str(previous.get("github_repository_id"))!=job.github_repository_id:
                    raise SyncError("Webhook 配置或仓库绑定已变化，未读取远程数据。")
        snapshot=await asyncio.wait_for(fetcher(name,previous) if fetcher else fetch_current(settings,name,previous,scopes),timeout=120)
        with SessionLocal() as db:
            state=db.get(RepositorySyncState,repo_id);now=utcnow()
            times={**state.section_times,**{name:now.isoformat() for name in scopes}}
            values={"status":"ready","message":"仓库快照已刷新","lease_token":None,"lease_until":None,"section_times":times,"sequence":RepositorySyncState.sequence+1,"updated_at":now}
            if set(scopes)==SCOPES:values["last_full_sync_at"]=now
            # Commit the snapshot and receipt together only while this writer owns the lease.
            changed=db.execute(update(RepositorySyncState).where(RepositorySyncState.repository_id==repo_id,
                RepositorySyncState.lease_token==token,RepositorySyncState.lease_until>now).values(**values).execution_options(synchronize_session=False))
            if changed.rowcount!=1:raise SyncError("同步租约已失效，旧结果没有覆盖当前快照。")
            if delivery_id and (not settings.github_webhook_enabled or repo_id not in allowed_ids(settings)):
                raise SyncError("Webhook 仓库授权已变化，旧结果没有写入。")
            snapshot["sync_info"]={**snapshot.get("sync_info",{}),"trigger":"webhook" if delivery_id else "manual","refreshed_sections":sorted(scopes),"section_times":times}
            repo=db.get(Repository,repo_id);repo.snapshot=snapshot;repo.synced_at=now
            sync_snapshot_documents(db,repo,settings)
            if delivery_id:
                job=db.get(WebhookDelivery,delivery_id)
                if job.lease_token!=token:raise SyncError("事件领取已失效，旧结果没有写入。")
                job.status="succeeded";job.message="已按事件刷新 GitHub 当前状态";job.lease_token=job.lease_until=None;job.updated_at=now
            db.commit()
    except asyncio.CancelledError:
        release_claim(repo_id,token,"interrupted","Worker 已停止，读取任务等待重新领取",delivery_id,requeue=True)
        raise
    except Exception as exc:
        message=str(exc) if isinstance(exc,SyncError) else "刷新失败，原快照保留；请核对 GitHub 连接后重试。"
        release_claim(repo_id,token,"failed",message,delivery_id)
        raise


async def manual_refresh(settings,repo_id,fetcher):
    with SessionLocal() as db:
        ensure_state(db,repo_id);token=take_lease(db,repo_id)
        if not token:raise SyncError("该仓库已有同步正在执行，请稍后再试。")
        db.commit()
    await refresh_claim(settings,repo_id,token,sorted(SCOPES),fetcher=fetcher)


def claim_delivery(settings):
    if not settings.github_webhook_enabled or settings.devflow_mode!="live":return None
    ids=allowed_ids(settings)
    with SessionLocal() as db:
        now=utcnow()
        for job in db.scalars(select(WebhookDelivery).where(WebhookDelivery.status=="running",WebhookDelivery.lease_until<=now)):
            token=job.lease_token
            changed=db.execute(update(WebhookDelivery).where(WebhookDelivery.id==job.id,WebhookDelivery.status=="running",WebhookDelivery.lease_token==token,WebhookDelivery.lease_until<=now).values(status="queued" if job.attempts<3 else "failed",lease_until=None,lease_token=None,message="前次读取租约已过期",updated_at=now).execution_options(synchronize_session=False))
            if changed.rowcount==1:
                db.execute(update(RepositorySyncState).where(RepositorySyncState.repository_id==job.repository_id,RepositorySyncState.lease_token==token).values(lease_until=None,lease_token=None,status="interrupted",message="读取租约已过期",updated_at=now))
        db.commit()
        db.expire_all()
        candidates=list(db.scalars(select(WebhookDelivery).where(WebhookDelivery.status=="queued",WebhookDelivery.repository_id.in_(ids),WebhookDelivery.attempts<3).order_by(WebhookDelivery.created_at).limit(10)))
        for job in candidates:
            ensure_state(db,job.repository_id);token=take_lease(db,job.repository_id)
            if not token:db.rollback();continue
            changed=db.execute(update(WebhookDelivery).where(WebhookDelivery.id==job.id,WebhookDelivery.status=="queued",WebhookDelivery.attempts<3).values(status="running",attempts=WebhookDelivery.attempts+1,lease_token=token,lease_until=now+timedelta(seconds=LEASE_SECONDS),message="后台读取 GitHub 当前状态",updated_at=now))
            if changed.rowcount!=1:db.rollback();continue
            result={"id":job.id,"repository_id":job.repository_id,"token":token,"scopes":job.scopes}
            db.commit();return result
    return None


async def execute_delivery(claim,settings):
    try:await refresh_claim(settings,claim["repository_id"],claim["token"],claim["scopes"],delivery_id=claim["id"])
    except asyncio.CancelledError:raise
    except Exception:pass  # Status/message are already saved; no payload or credential logging.
