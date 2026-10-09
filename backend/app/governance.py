"""Human-reviewed memories, draft revisions, local audit and authenticated users."""
import hashlib
import secrets
from datetime import timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from .db import (ActionDraft, AgentRun, AppUser, AuditRecord, AuthSession, AuthThrottle,
                 DraftReview, ProjectMemory, PublishAttempt, Repository, RepositoryMember, get_db, utcnow)
from .knowledge import bm25
from .security import audit, identity, password_hash, password_matches, role_for, runtime_settings

router = APIRouter(prefix="/api")


class Credentials(BaseModel):
    username: str = Field(pattern=r"^[a-zA-Z0-9_.-]{3,40}$")
    password: str = Field(min_length=1, max_length=256)


class NewUser(Credentials):
    password: str = Field(min_length=12, max_length=256)


class MemberInput(BaseModel):
    user_id: str
    role: Literal["viewer", "editor", "maintainer", "none"]


class MemoryInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=2000)
    run_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=16)
    @field_validator("title", "content")
    @classmethod
    def nonblank(cls, value):
        if not value.strip(): raise ValueError("内容不能为空。")
        return value.strip()


class MemoryEdit(MemoryInput):
    version: int = Field(ge=1)


class ReviewInput(BaseModel):
    version: int = Field(ge=1)
    decision: Literal["approve", "reject"]
    note: str = Field(default="", max_length=1000)


class VersionInput(BaseModel):
    version: int = Field(ge=1)


class MemorySearch(BaseModel):
    query: str = Field(min_length=1,max_length=1000)


class DraftEdit(BaseModel):
    version: int = Field(ge=1)
    body: str = Field(min_length=1, max_length=30000)
    target_kind: Literal["issue", "pr"] | None = None
    target_number: int | None = Field(default=None, gt=0)


def repository(db, ident, settings):
    repo = db.get(Repository, ident)
    if not repo: raise HTTPException(404, "仓库不存在。")
    if repo.snapshot.get("source") != ("demo" if settings.devflow_mode == "demo" else "github"):
        raise HTTPException(409, "仓库来源与当前模式不同。")
    return repo


def reject_credentials(text, settings):
    for field in (settings.llm_api_key, settings.github_token, settings.embedding_api_key,
                  settings.rerank_api_key, settings.milvus_token, settings.bootstrap_admin_password, settings.mcp_access_token, settings.github_webhook_secret):
        value = field.get_secret_value()
        if value and value in text: raise HTTPException(422, "内容包含本地配置密钥，请移除后保存。")


@router.get("/auth/session")
def auth_session(request: Request, db=Depends(get_db), settings=Depends(runtime_settings)):
    return {"auth_enabled":settings.auth_enabled, "user":identity(request, db, settings)}


@router.post("/auth/login")
def login(body: Credentials, request: Request, response: Response, db=Depends(get_db), settings=Depends(runtime_settings)):
    if not settings.auth_enabled: raise HTTPException(409, "当前为本地模式，无需登录。")
    key = hashlib.sha256((body.username + ":" + (request.client.host if request.client else "unknown")).encode()).hexdigest()
    guard = db.get(AuthThrottle, key)
    now = utcnow()
    if guard and guard.window_start.replace(tzinfo=timezone.utc) < now - timedelta(minutes=15):
        guard.count, guard.window_start = 0, now
    if guard and guard.count >= 8: raise HTTPException(429, "尝试次数过多，请 15 分钟后重试。")
    user = db.scalar(select(AppUser).where(AppUser.username == body.username, AppUser.active.is_(True)))
    # A fixed dummy hash keeps nonexistent-user failures on the same password-hashing path.
    valid = password_matches(body.password, user.password_hash if user else DUMMY_HASH)
    if not user or not valid:
        if not guard:
            guard = AuthThrottle(key=key, count=0, window_start=now); db.add(guard)
        guard.count += 1; db.commit()
        raise HTTPException(401, "用户名或密码错误。")
    if guard: db.delete(guard)
    db.execute(delete(AuthSession).where(AuthSession.expires_at <= now))
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id, expires_at=now + timedelta(hours=12)))
    audit(db, {"username":user.username}, "auth.login", user.id)
    db.commit()
    response.set_cookie("devflow_session", token, max_age=43200, httponly=True, samesite="strict", secure=settings.auth_cookie_secure, path="/")
    return {"username":user.username, "is_admin":user.is_admin}


DUMMY_HASH = password_hash("never-used-dummy-login")


@router.post("/auth/logout")
def logout(request: Request, response: Response, db=Depends(get_db)):
    token = request.cookies.get("devflow_session", "")
    if token: db.execute(delete(AuthSession).where(AuthSession.token_hash == hashlib.sha256(token.encode()).hexdigest()))
    db.commit(); response.delete_cookie("devflow_session", path="/")
    return {"logged_out":True}


@router.get("/admin/users")
def users(db=Depends(get_db)):
    return [{"id":u.id,"username":u.username,"is_admin":u.is_admin,"active":u.active} for u in db.scalars(select(AppUser))]


@router.post("/admin/users", status_code=201)
def add_user(body: NewUser, request: Request, db=Depends(get_db)):
    if db.scalar(select(AppUser.id).where(AppUser.username == body.username)): raise HTTPException(409, "用户名已存在。")
    user = AppUser(username=body.username, password_hash=password_hash(body.password)); db.add(user)
    try: db.flush()
    except IntegrityError: db.rollback(); raise HTTPException(409,"用户名已存在。") from None
    audit(db, request.state.actor, "user.create", user.id)
    db.commit()
    return {"id":user.id,"username":user.username,"is_admin":False,"active":True}


@router.post("/admin/users/{user_id}/disable")
def disable_user(user_id: str, request: Request, db=Depends(get_db)):
    user = db.get(AppUser, user_id)
    if not user: raise HTTPException(404, "用户不存在。")
    if user.is_admin: raise HTTPException(409, "当前界面不支持停用管理员。")
    user.active = False
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    audit(db, request.state.actor, "user.disable", user.id); db.commit()
    return {"active":False}


@router.get("/admin/repositories/{repository_id}/members")
def members(repository_id: str, db=Depends(get_db)):
    return [{"user_id":m.user_id,"username":u.username,"role":m.role} for m,u in db.execute(select(RepositoryMember, AppUser).join(AppUser).where(RepositoryMember.repository_id == repository_id))]


@router.post("/admin/repositories/{repository_id}/members")
def set_member(repository_id: str, body: MemberInput, request: Request, db=Depends(get_db)):
    if not db.get(Repository, repository_id) or not db.get(AppUser, body.user_id): raise HTTPException(404, "仓库或用户不存在。")
    member = db.get(RepositoryMember, (repository_id, body.user_id))
    if body.role == "none":
        if member: db.delete(member)
    elif member: member.role = body.role
    else: db.add(RepositoryMember(repository_id=repository_id, user_id=body.user_id, role=body.role))
    audit(db, request.state.actor, "membership.set", body.user_id, repository_id, role=body.role); db.commit()
    return {"role":body.role}


@router.get("/repositories/{repository_id}/governance")
def governance(repository_id: str, request: Request, db=Depends(get_db), settings=Depends(runtime_settings)):
    repository(db, repository_id, settings)
    role = role_for(db, request.state.actor, repository_id)
    return {"role":role, "can_edit":role != "viewer", "can_review":role in ("admin","maintainer"),
            "publish_enabled":settings.devflow_mode=="live" and settings.auth_enabled and settings.github_write_enabled and bool(settings.github_token.get_secret_value()),
            "auth_enabled":settings.auth_enabled}


def memory_json(memory):
    return {key:getattr(memory,key) for key in ("id","title","content","run_id","evidence_ids","status","version","author","approved_by","updated_at")}


def memory_sources(db, repo, body):
    if not body.run_id:
        if body.evidence_ids: raise HTTPException(422, "引用证据需要指定来源运行。")
        return
    run = db.get(AgentRun, body.run_id)
    if not run or run.repository_id != repo or run.status != "completed" or not run.result:
        raise HTTPException(409, "只能引用当前仓库已完成的运行。")
    allowed = {e["id"] for e in run.result["evidence"]}
    if any(e not in allowed for e in body.evidence_ids): raise HTTPException(422, "记忆引用了无效证据。")


def memory_corpus(db, repo):
    memories = list(db.scalars(select(ProjectMemory).where(ProjectMemory.repository_id == repo, ProjectMemory.status == "approved").order_by(ProjectMemory.updated_at.desc()).limit(50)))
    return [{"id":f"memory:{m.id}:v{m.version}","chunk_id":f"memory:{m.id}:v{m.version}","title":m.title,
             "content":m.content,"heading":"人工批准的项目记忆","source":"approved_memory","revision":str(m.version),"url":"",
             "path":f"memory/{m.id}","citation":f"人工批准的项目记忆 · v{m.version}","approved_by":m.approved_by} for m in memories]


@router.get("/repositories/{repository_id}/memories")
def list_memories(repository_id: str, db=Depends(get_db)):
    return [memory_json(m) for m in db.scalars(select(ProjectMemory).where(ProjectMemory.repository_id == repository_id).order_by(ProjectMemory.updated_at.desc()).limit(100))]


@router.post("/repositories/{repository_id}/memories/search")
def search_memories(repository_id: str, body: MemorySearch, db=Depends(get_db)):
    return {"results":bm25(memory_corpus(db,repository_id),body.query,3)}


@router.post("/repositories/{repository_id}/memories", status_code=201)
def create_memory(repository_id: str, body: MemoryInput, request: Request, db=Depends(get_db), settings=Depends(runtime_settings)):
    repository(db, repository_id, settings); memory_sources(db, repository_id, body)
    reject_credentials(body.title + body.content, settings)
    memory = ProjectMemory(repository_id=repository_id, **body.model_dump(), author=request.state.actor["username"])
    db.add(memory); db.flush(); audit(db, request.state.actor, "memory.create", memory.id, repository_id, version=1)
    db.commit(); return memory_json(memory)


def get_memory(db, repo, ident):
    memory = db.get(ProjectMemory, ident)
    if not memory or memory.repository_id != repo: raise HTTPException(404, "记忆不存在。")
    return memory


@router.post("/repositories/{repository_id}/memories/{memory_id}/edit")
def edit_memory(repository_id: str, memory_id: str, body: MemoryEdit, request: Request, db=Depends(get_db), settings=Depends(runtime_settings)):
    get_memory(db, repository_id, memory_id); memory_sources(db, repository_id, body)
    reject_credentials(body.title + body.content, settings)
    changed = db.execute(update(ProjectMemory).where(ProjectMemory.id == memory_id, ProjectMemory.version == body.version).values(**body.model_dump(exclude={"version"}), version=body.version+1, status="candidate", approved_by=None, updated_at=utcnow()))
    if changed.rowcount != 1: raise HTTPException(409, "记忆版本已变化，请刷新。")
    audit(db, request.state.actor, "memory.edit", memory_id, repository_id, version=body.version+1)
    db.commit(); return memory_json(db.get(ProjectMemory,memory_id))


@router.post("/repositories/{repository_id}/memories/{memory_id}/review")
def review_memory(repository_id: str, memory_id: str, body: ReviewInput, request: Request, db=Depends(get_db), settings=Depends(runtime_settings)):
    get_memory(db, repository_id, memory_id); reject_credentials(body.note, settings)
    status = "approved" if body.decision == "approve" else "rejected"
    changed = db.execute(update(ProjectMemory).where(ProjectMemory.id == memory_id, ProjectMemory.version == body.version, ProjectMemory.status == "candidate").values(status=status, version=body.version+1, approved_by=request.state.actor["username"] if status == "approved" else None, updated_at=utcnow()))
    if changed.rowcount != 1: raise HTTPException(409, "记忆状态或版本已变化，请刷新。")
    audit(db, request.state.actor, "memory." + body.decision, memory_id, repository_id, version=body.version+1, note=body.note)
    db.commit(); return memory_json(db.get(ProjectMemory,memory_id))


@router.post("/repositories/{repository_id}/memories/{memory_id}/archive")
def archive_memory(repository_id: str, memory_id: str, body: VersionInput, request: Request, db=Depends(get_db)):
    get_memory(db, repository_id, memory_id)
    changed = db.execute(update(ProjectMemory).where(ProjectMemory.id == memory_id, ProjectMemory.version == body.version).values(status="archived", version=body.version+1, updated_at=utcnow()))
    if changed.rowcount != 1: raise HTTPException(409, "记忆版本已变化，请刷新。")
    audit(db, request.state.actor, "memory.archive", memory_id, repository_id, version=body.version+1); db.commit()
    return memory_json(db.get(ProjectMemory,memory_id))


def get_draft(db, repo, ident):
    draft = db.get(ActionDraft, ident)
    if not draft or draft.repository_id != repo: raise HTTPException(404, "草稿不存在。")
    return draft


def draft_state(db, draft):
    review = db.get(DraftReview, draft.id)
    if not review:
        review = DraftReview(draft_id=draft.id); db.add(review)
        try: db.flush()
        except IntegrityError: db.rollback(); raise HTTPException(409, "草稿已被另一个请求更新，请刷新。")
    return review


def draft_digest(draft, review):
    import json
    return hashlib.sha256(json.dumps({"body":draft.body,"repo":draft.repository_id,"target_kind":review.target_kind,
                                      "target_number":review.target_number,"sha":review.expected_sha},sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def draft_json(db, draft):
    review, attempt = db.get(DraftReview,draft.id), db.get(PublishAttempt,draft.id)
    data = {key:getattr(draft,key) for key in ("id","run_id","body","status","created_at")}
    data.update(version=review.version if review else 1,target_kind=review.target_kind if review else None,
                target_number=review.target_number if review else None,expected_sha=review.expected_sha if review else None,
                approved_by=review.approved_by if review else None,note=review.note if review else "",
                published_url=attempt.url if attempt else None,publish_message=attempt.message if attempt else "")
    return data


@router.post("/repositories/{repository_id}/drafts/{draft_id}/edit")
def edit_draft(repository_id: str, draft_id: str, body: DraftEdit, request: Request, db=Depends(get_db), settings=Depends(runtime_settings)):
    draft = get_draft(db,repository_id,draft_id); review = draft_state(db,draft)
    if draft.status in ("publishing","published","uncertain"): raise HTTPException(409,"发布中的草稿或已有发布记录不能修改。")
    if bool(body.target_kind) != bool(body.target_number) or not body.body.strip(): raise HTTPException(422,"请填写完整目标和草稿正文。")
    reject_credentials(body.body,settings)
    changed = db.execute(update(DraftReview).where(DraftReview.draft_id == draft.id, DraftReview.version == body.version).values(version=body.version+1,target_kind=body.target_kind,target_number=body.target_number,expected_sha=None,approved_digest=None,approved_by=None,note=""))
    if changed.rowcount != 1: raise HTTPException(409,"草稿版本已变化，请刷新。")
    draft.body, draft.status = body.body.strip(), "draft"
    audit(db,request.state.actor,"draft.edit",draft.id,repository_id,version=body.version+1); db.commit()
    return draft_json(db,draft)


async def verified_target(db, draft, review, settings):
    if not review.target_kind: return None
    run = db.get(AgentRun,draft.run_id)
    ident = f"{review.target_kind}:{review.target_number}"
    evidence = next((e for e in run.result["evidence"] if e["id"] == ident),None)
    if not evidence: raise HTTPException(409,"原分析没有该目标的证据，请先分析对应 Issue 或 PR。")
    if settings.devflow_mode == "demo":
        from .demo import demo_pr
        return demo_pr(review.target_number)["head_sha"] if review.target_kind == "pr" else None
    from .github import GitHubClient, GitHubError
    client = GitHubClient(settings)
    try:
        repo = db.get(Repository,draft.repository_id)
        path = "pulls" if review.target_kind == "pr" else "issues"
        current = await client.get(f"/repos/{repo.full_name}/{path}/{review.target_number}")
        if current.get("state") != "open": raise HTTPException(409,"目标已关闭，请重新分析。")
        if review.target_kind == "issue" and current.get("pull_request"): raise HTTPException(409,"目标是 PR，请选择 PR。")
        sha = current["head"]["sha"] if review.target_kind == "pr" else None
        if sha and sha != evidence.get("sha"): raise HTTPException(409,"PR head SHA 已变化，原分析不能继续审批或发布，请重新分析。")
        if review.expected_sha and sha != review.expected_sha: raise HTTPException(409,"PR 提交与获批版本不同，请重新分析。")
        return sha
    except GitHubError as exc: raise HTTPException(502,str(exc)) from exc
    finally: await client.close()


@router.post("/repositories/{repository_id}/drafts/{draft_id}/submit")
async def submit_draft(repository_id: str, draft_id: str, body: VersionInput, request: Request, db=Depends(get_db), settings=Depends(runtime_settings)):
    draft = get_draft(db,repository_id,draft_id); review = draft_state(db,draft)
    if draft.status not in ("draft","rejected"): raise HTTPException(409,"当前草稿不能重复提交审核。")
    sha = await verified_target(db,draft,review,settings)
    changed = db.execute(update(DraftReview).where(DraftReview.draft_id==draft.id,DraftReview.version==body.version).values(version=body.version+1,expected_sha=sha,approved_digest=None,approved_by=None))
    if changed.rowcount != 1: raise HTTPException(409,"草稿版本已变化，请刷新。")
    draft.status = "pending"
    audit(db,request.state.actor,"draft.submit",draft.id,repository_id,version=body.version+1); db.commit()
    return draft_json(db,draft)


@router.post("/repositories/{repository_id}/drafts/{draft_id}/review")
async def review_draft(repository_id: str, draft_id: str, body: ReviewInput, request: Request, db=Depends(get_db), settings=Depends(runtime_settings)):
    draft = get_draft(db,repository_id,draft_id); review = draft_state(db,draft)
    if draft.status != "pending" or review.version != body.version: raise HTTPException(409,"草稿状态或版本已变化，请刷新。")
    reject_credentials(body.note,settings)
    if body.decision == "approve": await verified_target(db,draft,review,settings)
    changed=db.execute(update(DraftReview).where(DraftReview.draft_id==draft.id,DraftReview.version==body.version).values(version=body.version+1,note=body.note,approved_by=request.state.actor["username"] if body.decision=="approve" else None))
    if changed.rowcount != 1: raise HTTPException(409,"草稿版本已变化，请刷新。")
    draft.status="approved" if body.decision=="approve" else "rejected"
    db.flush(); review=db.get(DraftReview,draft.id)
    review.approved_digest=draft_digest(draft,review) if body.decision=="approve" else None
    audit(db,request.state.actor,"draft."+body.decision,draft.id,repository_id,version=body.version+1,note=body.note)
    db.commit(); return draft_json(db,draft)


@router.get("/repositories/{repository_id}/audit")
def list_audit(repository_id: str, db=Depends(get_db)):
    return [{"id":a.id,"actor":a.actor,"action":a.action,"object_id":a.object_id,"detail":a.detail,"created_at":a.created_at} for a in db.scalars(select(AuditRecord).where(AuditRecord.repository_id==repository_id).order_by(AuditRecord.created_at.desc()).limit(100))]
