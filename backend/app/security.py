"""Server-side sessions and repository roles; local mode is explicitly unauthenticated."""
import hashlib
import hmac
import secrets
import re
from datetime import timedelta, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select

from .db import AppUser, AuthSession, AuditRecord, Repository, RepositoryMember, get_db, utcnow

ROLES = {"viewer":1, "editor":2, "maintainer":3, "admin":3}


def password_hash(password):
    salt = secrets.token_bytes(16)
    value = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex()
    return salt.hex() + ":" + value


def password_matches(password, stored):
    salt, value = stored.split(":")
    derived = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return hmac.compare_digest(derived, value)


def audit(db, actor, action, ident, repo=None, **detail):
    db.add(AuditRecord(repository_id=repo, actor=actor["username"], action=action, object_id=ident, detail=detail))


def bootstrap(db, settings):
    if not settings.auth_enabled or db.scalar(select(AppUser.id).where(AppUser.is_admin.is_(True)).limit(1)): return
    password = settings.bootstrap_admin_password.get_secret_value()
    if len(password) < 12: raise RuntimeError("启用登录前，请配置至少 12 位的 BOOTSTRAP_ADMIN_PASSWORD。")
    if db.scalar(select(AppUser.id).where(AppUser.username==settings.bootstrap_admin_username)):
        raise RuntimeError("初始管理员用户名已被普通用户使用，请选择其他 BOOTSTRAP_ADMIN_USERNAME。")
    db.add(AppUser(username=settings.bootstrap_admin_username, password_hash=password_hash(password), is_admin=True))
    db.commit()


def runtime_settings(request: Request):
    return request.app.state.runtime_settings()


def identity(request, db, settings):
    authorization=request.headers.get("authorization", "")
    if authorization:
        value=settings.mcp_access_token.get_secret_value()
        if len(value)<32 or not hmac.compare_digest(authorization,"Bearer "+value):return None
        ids=[ident.strip() for ident in settings.mcp_repository_ids.split(",") if re.fullmatch(r"[0-9a-f]{32}",ident.strip())]
        return {"id":"mcp","username":"MCP 只读服务","is_admin":False,"authenticated":True,"mcp":True,"repository_ids":ids}
    if not settings.auth_enabled: return {"id":"local", "username":"本地管理员", "is_admin":True, "authenticated":False}
    token = request.cookies.get("devflow_session", "")
    if not token: return None
    session = db.get(AuthSession, hashlib.sha256(token.encode()).hexdigest())
    if not session or session.expires_at.replace(tzinfo=timezone.utc) <= utcnow(): return None
    user = db.get(AppUser, session.user_id)
    if not user or not user.active: return None
    return {"id":user.id, "username":user.username, "is_admin":user.is_admin, "authenticated":True}


def role_for(db, actor, repo):
    if actor.get("mcp"):return "viewer" if repo in actor["repository_ids"] else None
    if actor["is_admin"]: return "admin"
    membership = db.get(RepositoryMember, (repo, actor["id"]))
    return membership.role if membership else None


def accessible_repositories(db, actor):
    if actor.get("mcp"):return actor["repository_ids"]
    if actor["is_admin"]: return None
    return list(db.scalars(select(RepositoryMember.repository_id).where(RepositoryMember.user_id == actor["id"])))


async def authorize(request: Request, db=Depends(get_db), settings=Depends(runtime_settings)):
    path = request.url.path
    if not path.startswith("/api/"): return
    if request.method != "GET":
        origin = request.headers.get("origin")
        if origin and origin not in settings.cors_origins.split(","):
            raise HTTPException(403, "请求来源未获允许。")
    # This exact service endpoint uses raw-body HMAC instead of user sessions.
    if path=="/api/webhooks/github" and request.method=="POST":return
    actor = identity(request, db, settings)
    request.state.actor = actor
    if actor and actor.get("mcp"):
        read_path=re.fullmatch(r"/api/repositories/[0-9a-f]{32}/(?:snapshot|runs(?:/[0-9a-f]{32})?|reports|metrics|delivery/status|code/(?:files|file)|knowledge/status)",path)
        search_path=re.fullmatch(r"/api/repositories/[0-9a-f]{32}/(?:knowledge|memories|code)/search",path)
        if not ((request.method=="GET" and (path=="/api/repositories" or read_path)) or (request.method=="POST" and search_path)):
            raise HTTPException(403,"MCP 服务只允许授权仓库的白名单读取，不允许创建、批准或发布。")
    if path in ("/api/health", "/api/auth/session", "/api/auth/login", "/api/auth/logout"): return
    if not actor: raise HTTPException(401, "请先登录。")
    if path.startswith("/api/admin/"):
        if not actor["is_admin"]: raise HTTPException(403, "需要管理员权限。")
        return
    repo = request.path_params.get("repository_id")
    if path.startswith("/api/chat/"):
        try: repo = (await request.json()).get("repository_id")
        except ValueError: raise HTTPException(422, "请求内容格式无效。")
    if repo:
        role = role_for(db, actor, repo)
        if not role: raise HTTPException(404, "仓库不存在或无访问权限。")
        record=db.get(Repository,repo)
        if not record: raise HTTPException(404,"仓库不存在。")
        if record.snapshot.get("source") != ("demo" if settings.devflow_mode=="demo" else "github"):
            raise HTTPException(409,"仓库来源与当前运行模式不同。")
        required = 1
        if request.method != "GET" and not path.endswith(("/search",)):
            required = 2
        if path.endswith(("/review", "/publish", "/reconcile", "/audit", "/publish-knowledge")): required = 3
        if request.method != "GET" and path.endswith("/answer-reviews"): required = 3
        if ROLES[role] < required: raise HTTPException(403, "当前仓库角色无权执行此操作。")
    elif path == "/api/repositories" and request.method != "GET" and not actor["is_admin"]:
        raise HTTPException(403, "新增仓库需要管理员权限。")
