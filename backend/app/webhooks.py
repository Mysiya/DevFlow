"""GitHub HMAC inbox. Payloads only select a bounded refresh; never become snapshot state."""
import asyncio
import hashlib
import hmac
import json
import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from .db import Repository, RepositorySyncState, WebhookDelivery, get_db, utcnow
from .security import audit, runtime_settings
from .synchronization import allowed_ids

router=APIRouter()
MAX_BODY=1_048_576


def event_scopes(event,payload,branch):
    action=payload.get("action","")
    if not isinstance(action,str) or not re.fullmatch(r"[a-z_]{0,40}",action):raise HTTPException(422,"事件 action 无效。")
    if event=="issues" and action in {"opened","edited","closed","reopened","deleted","labeled","unlabeled","assigned","unassigned","transferred","milestoned","demilestoned"}:return ["issues"]
    if event=="pull_request" and action in {"opened","edited","closed","reopened","synchronize","ready_for_review","converted_to_draft","labeled","unlabeled","assigned","unassigned"}:return ["issues","pulls"]
    if event=="workflow_run" and action in {"requested","in_progress","completed"}:return ["runs"]
    if event=="push" and payload.get("ref")=="refs/heads/"+branch:return ["documents","repository"]
    return []


async def bounded_body(request):
    chunks=[];size=0
    async for chunk in request.stream():
        size+=len(chunk)
        if size>MAX_BODY:raise HTTPException(413,"Webhook 请求超过 1 MB 上限。")
        chunks.append(chunk)
    return b"".join(chunks)


def duplicate_response(job,repo_id,event,body_hash):
    if job.repository_id!=repo_id or job.event!=event or job.body_hash!=body_hash:raise HTTPException(409,"投递 ID 已用于不同事件。")
    return {"status":"duplicate","delivery_id":job.id,"processing_status":job.status}


@router.post("/api/webhooks/github",status_code=202)
async def receive(request:Request,db=Depends(get_db),settings=Depends(runtime_settings)):
    if not settings.github_webhook_enabled or settings.devflow_mode!="live":raise HTTPException(503,"GitHub Webhook 未启用。")
    if request.headers.get("origin"):raise HTTPException(403,"此接口仅接收已签名的服务投递。")
    if request.headers.get("content-type","").split(";")[0].strip().lower()!="application/json" or request.headers.get("content-encoding","") not in ("","identity"):
        raise HTTPException(415,"Webhook 需要未压缩的 application/json。")
    length=request.headers.get("content-length")
    if length and (not length.isdigit() or int(length)>MAX_BODY):raise HTTPException(413,"Webhook 请求长度无效或超过上限。")
    signature=request.headers.get("x-hub-signature-256","")
    if not re.fullmatch(r"sha256=[0-9a-f]{64}",signature):raise HTTPException(403,"Webhook 签名无效。")
    try:body=await asyncio.wait_for(bounded_body(request),timeout=5)
    except asyncio.TimeoutError:raise HTTPException(408,"Webhook 请求读取超时。")
    expected="sha256="+hmac.new(settings.github_webhook_secret.get_secret_value().encode(),body,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature,expected):raise HTTPException(403,"Webhook 签名无效。")
    try:
        delivery=str(UUID(request.headers.get("x-github-delivery","")))
        payload=json.loads(body)
    except (ValueError,UnicodeDecodeError):raise HTTPException(422,"投递 ID 或 JSON 无效。")
    event=request.headers.get("x-github-event","")
    if not re.fullmatch(r"[a-z_]{1,40}",event) or not isinstance(payload,dict):raise HTTPException(422,"事件类型或内容无效。")
    remote=payload.get("repository")
    if not isinstance(remote,dict):raise HTTPException(422,"事件缺少 repository。")
    ident,name=remote.get("id"),remote.get("full_name")
    if isinstance(ident,bool) or not isinstance(ident,int) or not 0<ident<10**20 or not isinstance(name,str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",name):
        raise HTTPException(422,"仓库标识无效。")
    repo=db.scalar(select(Repository).where(func.lower(Repository.full_name)==name.lower()))
    if not repo or repo.id not in allowed_ids(settings) or repo.snapshot.get("source")!="github" or repo.snapshot.get("github_repository_id")!=ident:
        raise HTTPException(403,"仓库未绑定或不在 Webhook 白名单。")
    scopes=event_scopes(event,payload,repo.snapshot.get("branch", ""))
    body_hash=hashlib.sha256(body).hexdigest()
    existing=db.get(WebhookDelivery,delivery)
    if existing:return duplicate_response(existing,repo.id,event,body_hash)
    if db.scalar(select(func.count()).select_from(WebhookDelivery).where(WebhookDelivery.status.in_(("queued","running"))))>=100:
        raise HTTPException(503,"Webhook 后台队列已满，请稍后重新投递。")
    if db.scalar(select(func.count()).select_from(WebhookDelivery))>=10000:
        raise HTTPException(503,"Webhook 历史记录已达开发容量上限。")
    job=WebhookDelivery(id=delivery,repository_id=repo.id,event=event,action=payload.get("action",""),body_hash=body_hash,
        github_repository_id=str(ident),scopes=scopes,status="queued" if scopes else "ignored",message="等待后台刷新" if scopes else "未订阅的事件/action 或非默认分支，未刷新")
    db.add(job)
    try:db.commit()
    except IntegrityError:
        db.rollback();return duplicate_response(db.get(WebhookDelivery,delivery),repo.id,event,body_hash)
    return {"status":job.status,"delivery_id":job.id}


def delivery_json(job):
    return {key:getattr(job,key) for key in ("id","event","action","scopes","status","attempts","message","created_at","updated_at")}


@router.get("/api/repositories/{repository_id}/sync/status")
def sync_status(repository_id:str,db=Depends(get_db),settings=Depends(runtime_settings)):
    repo=db.get(Repository,repository_id);state=db.get(RepositorySyncState,repository_id)
    bound=bool(repo.snapshot.get("github_repository_id"))
    enabled=settings.github_webhook_enabled and repository_id in allowed_ids(settings) and bound
    jobs=list(db.scalars(select(WebhookDelivery).where(WebhookDelivery.repository_id==repository_id).order_by(WebhookDelivery.created_at.desc()).limit(20)))
    counts=dict(db.execute(select(WebhookDelivery.status,func.count()).where(WebhookDelivery.repository_id==repository_id).group_by(WebhookDelivery.status)).all())
    return {"webhook_enabled":enabled,"github_repository_bound":bound,"endpoint":"/api/webhooks/github","status":state.status if state else "idle",
        "message":state.message if state else "历史同步未记录分项时间，可手动同步一次。","sequence":state.sequence if state else 0,
        "last_full_sync_at":state.last_full_sync_at if state else None,"section_times":state.section_times if state else {},"counts":counts,"deliveries":[delivery_json(job) for job in jobs]}


@router.post("/api/repositories/{repository_id}/sync/deliveries/{delivery_id}/retry")
def retry_delivery(repository_id:str,delivery_id:str,request:Request,db=Depends(get_db),settings=Depends(runtime_settings)):
    if not settings.github_webhook_enabled or repository_id not in allowed_ids(settings):raise HTTPException(409,"当前仓库未启用 Webhook。")
    job=db.get(WebhookDelivery,delivery_id)
    if not job or job.repository_id!=repository_id:raise HTTPException(404,"投递记录不存在。")
    changed=db.execute(update(WebhookDelivery).where(WebhookDelivery.id==delivery_id,WebhookDelivery.status=="failed",WebhookDelivery.attempts<3).values(status="queued",message="已请求重试只读刷新",updated_at=utcnow()))
    if changed.rowcount!=1:raise HTTPException(409,"只允许失败且未满 3 次领取的事件重试；其他情况请手动同步仓库。")
    audit(db,request.state.actor,"webhook.retry",delivery_id,repository_id)
    db.commit();return {"status":"queued","delivery_id":delivery_id}
