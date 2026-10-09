"""Human-triggered comments; this writer is never exposed to Agent tools."""
import asyncio
from datetime import timedelta
import httpx
from fastapi import Depends, HTTPException, Request
from sqlalchemy import update
from .db import ActionDraft, AgentRun, DraftReview, PublishAttempt, Repository, SessionLocal, get_db, new_id, utcnow
from .governance import VersionInput, draft_digest, draft_json, draft_state, get_draft, reject_credentials, router, verified_target
from .security import audit, runtime_settings


class CommentClient:
    def __init__(self, settings):
        self.client = httpx.AsyncClient(base_url="https://api.github.com", timeout=20, follow_redirects=False,
            headers={"Authorization":"Bearer " + settings.github_token.get_secret_value(),
                     "Accept":"application/vnd.github+json", "User-Agent":"DevFlow-AI"})
    async def find(self, repo, number, marker, expected_body):
        found = None
        for page in range(1, 6):
            response=await self.client.get(f"/repos/{repo}/issues/{number}/comments",params={"per_page":100,"page":page})
            response.raise_for_status(); comments=response.json()
            for c in comments:
                if marker in c.get("body",""):
                    if found or c["body"] != expected_body:raise ValueError("远程评论标记或正文不一致，需人工核对。")
                    found=c
            if len(comments)<100:return found
        raise ValueError("评论超过核对范围，不能确认是否已发布，请人工核对。")
    async def post(self, repo, number, body):
        response=await self.client.post(f"/repos/{repo}/issues/{number}/comments",json={"body":body})
        response.raise_for_status(); return response.json()
    async def close(self):await self.client.aclose()


def ready(settings):
    if not settings.auth_enabled or not settings.github_write_enabled or not settings.github_token.get_secret_value():
        raise HTTPException(409,"GitHub 写回未启用。请先启用登录、配置写权限 Token 和 GITHUB_WRITE_ENABLED。")


def validate_comment(comment, repo):
    if not isinstance(comment.get("id"),int) or comment["id"] <= 0 or not comment.get("html_url","").startswith(f"https://github.com/{repo}/"):
        raise ValueError("发布返回缺少有效评论标识。")


def finish(ident, token, actor, status, comment=None, message=""):
    with SessionLocal() as db:
        attempt=db.get(PublishAttempt,ident)
        if not attempt or attempt.token != token:return
        changed=db.execute(update(ActionDraft).where(ActionDraft.id==ident,ActionDraft.status.in_(("publishing","uncertain"))).values(status=status))
        if changed.rowcount != 1:return
        attempt.lease_until=None; attempt.message=message
        if comment:attempt.comment_id,attempt.url=comment["id"],comment["html_url"]
        draft=db.get(ActionDraft,ident)
        audit(db,actor,"draft."+status,ident,draft.repository_id,remote_id=attempt.comment_id,message=message)
        db.commit()


def recover_publications(db):
    for attempt in db.query(PublishAttempt).filter(PublishAttempt.lease_until <= utcnow()).all():
        changed=db.execute(update(ActionDraft).where(ActionDraft.id==attempt.draft_id,ActionDraft.status=="publishing").values(status="uncertain"))
        if changed.rowcount:
            attempt.lease_until=None; attempt.message="发布请求曾中断，请核对远程结果；不会自动重发。"
            draft=db.get(ActionDraft,attempt.draft_id)
            audit(db,{"username":"系统恢复"},"draft.uncertain",draft.id,draft.repository_id,message=attempt.message)
    db.commit()


@router.post("/repositories/{repository_id}/drafts/{draft_id}/publish")
async def publish(repository_id: str, draft_id: str, body: VersionInput, request: Request, db=Depends(get_db), settings=Depends(runtime_settings)):
    ready(settings); recover_publications(db)
    draft=get_draft(db,repository_id,draft_id); review=draft_state(db,draft)
    if draft.status=="published":return draft_json(db,draft)
    if draft.status!="approved" or review.version!=body.version or review.approved_digest!=draft_digest(draft,review):
        raise HTTPException(409,"只能发布当前版本、正文及目标均已获批的草稿。")
    run=db.get(AgentRun,draft.run_id)
    if run.mode!="live" or not review.target_kind or db.get(Repository,repository_id).snapshot.get("source")!="github":
        raise HTTPException(409,"仅真实仓库且含目标分析证据的获批草稿可发布；本地草稿可复制使用。")
    await verified_target(db,draft,review,settings); reject_credentials(draft.body,settings)
    digest=review.approved_digest
    changed=db.execute(update(DraftReview).where(DraftReview.draft_id==draft_id,DraftReview.version==body.version,DraftReview.approved_digest==digest).values(version=body.version+1))
    if changed.rowcount!=1:raise HTTPException(409,"草稿已变化或另一个请求已领取发布。")
    changed=db.execute(update(ActionDraft).where(ActionDraft.id==draft_id,ActionDraft.status=="approved").values(status="publishing"))
    if changed.rowcount!=1:db.rollback();raise HTTPException(409,"发布已被其他请求领取。")
    token=new_id(); db.add(PublishAttempt(draft_id=draft_id,token=token,digest=digest,lease_until=utcnow()+timedelta(seconds=120)))
    audit(db,request.state.actor,"draft.publish_claim",draft_id,repository_id,version=body.version+1)
    repo=db.get(Repository,repository_id).full_name; number=review.target_number
    marker=f"<!-- devflow:{draft_id}:{digest} -->"; text=draft.body+"\n\n"+marker
    db.commit()  # The claim is durable before any external POST.
    client=CommentClient(settings)
    try:
        comment=await client.find(repo,number,marker,text)
        if not comment:
            await verified_target(db,draft,review,settings)
            comment=await client.post(repo,number,text)
        validate_comment(comment, repo)
        finish(draft_id,token,request.state.actor,"published",comment)
    except (Exception,asyncio.CancelledError):
        finish(draft_id,token,request.state.actor,"uncertain",message="无法确认远程发布结果，请核对评论；不会自动重发。")
        if asyncio.current_task().cancelling():raise
    finally:await client.close()
    db.expire_all();return draft_json(db,db.get(ActionDraft,draft_id))


@router.post("/repositories/{repository_id}/drafts/{draft_id}/reconcile")
async def reconcile(repository_id: str,draft_id: str,request: Request,db=Depends(get_db),settings=Depends(runtime_settings)):
    ready(settings);recover_publications(db)
    draft=get_draft(db,repository_id,draft_id);attempt=db.get(PublishAttempt,draft_id)
    if draft.status=="published":return draft_json(db,draft)
    if draft.status!="uncertain" or not attempt:raise HTTPException(409,"只有发布结果待核对的草稿可执行此操作。")
    review=db.get(DraftReview,draft_id); marker=f"<!-- devflow:{draft_id}:{attempt.digest} -->"
    repo=db.get(Repository,repository_id).full_name; client=CommentClient(settings)
    try:
        comment=await client.find(repo,review.target_number,marker,draft.body+"\n\n"+marker)
        if comment:
            validate_comment(comment, repo)
            finish(draft_id,attempt.token,request.state.actor,"published",comment)
    except (httpx.HTTPError,ValueError):raise HTTPException(502,"远程核对未完成，请稍后重试；未发送评论。") from None
    finally:await client.close()
    db.expire_all();return draft_json(db,db.get(ActionDraft,draft_id))
