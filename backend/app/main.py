import asyncio
import contextlib
import json
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from .agents import safe_error
from .config import get_settings
from .db import ActionDraft, AgentRun, Base, Conversation, KnowledgeDocument, KnowledgeIndex, Repository, RunCheckpoint, RunJob, SessionLocal, engine, get_db, utcnow
from .demo import DEMO_REPO, demo_snapshot
from .github import GitHubClient, GitHubError
from .schemas import ChatInput, CodeSearchInput, DocumentInput, DraftInput, RepositoryInput, SearchInput, WorkspaceSyncInput
from .knowledge import digest, load_corpus, sync_snapshot_documents, upsert_document
from .retrieval import begin_index, build_index, search, status_for, service_status
from .vector import RetrievalError
from .workspace import WorkspaceError, WorkspaceManager
from .checkpoints import CheckpointError, claim_resume, create_checkpoint, recovery_info, validate_checkpoint
from .execution import ACTIVE_RUNS, stream_run
from .run_queue import enqueue, event_stream, expire_jobs, queue_status, request_stop
from .resumption import validate_sources
from .security import authorize, bootstrap, accessible_repositories, audit
from .governance import router as governance_router, draft_json, memory_corpus
from .publishing import recover_publications
from .delivery import router as delivery_router
from .webhooks import router as webhook_router
from .synchronization import manual_refresh, SyncError
from .fact_review import apply_review
from .answer_reviews import router as answer_review_router
from .prompts import freeze_binding
from .answer_comparisons import router as answer_comparison_router
from .prompt_experiments import router as prompt_experiment_router
from .answer_evaluation_sets import router as answer_evaluation_set_router
from .task_skills import catalog as skill_catalog, validate_selection
from .governance import reject_credentials
from .http_privacy import PrivateAPIResponses

settings = get_settings()


@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        bootstrap(db, settings)
        recover_publications(db)
        expire_jobs(db)
        # Interrupted streams are marked explicitly rather than left as perpetually running.
        for run in db.scalars(select(AgentRun).where(AgentRun.status == "running", ~select(RunJob.run_id).where(RunJob.run_id == AgentRun.id).exists())):
            run.status = "interrupted"
        if settings.devflow_mode == "demo" and not db.scalar(select(Repository).where(Repository.full_name == DEMO_REPO)):
            db.add(Repository(full_name=DEMO_REPO, snapshot=demo_snapshot(), synced_at=utcnow()))
        db.flush()
        for repo in db.scalars(select(Repository)):
            sync_snapshot_documents(db, repo, settings)
        for index in db.scalars(select(KnowledgeIndex).where(KnowledgeIndex.status == "indexing")):
            index.status, index.message = "failed", "服务曾中断索引任务，请重新建立索引。"
        db.commit()
    yield


app = FastAPI(title="DevFlow AI", version="0.18.0", lifespan=lifespan, dependencies=[Depends(authorize)])
app.state.runtime_settings = lambda: settings
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins.split(","), allow_methods=["GET", "POST"], allow_headers=["Content-Type"], allow_credentials=True)
app.add_middleware(PrivateAPIResponses)
app.include_router(governance_router)
app.include_router(delivery_router)
app.include_router(webhook_router)
app.include_router(answer_review_router)
app.include_router(answer_comparison_router)
app.include_router(prompt_experiment_router)
app.include_router(answer_evaluation_set_router)


def repo_or_404(db: Session, repository_id: str):
    repo = db.get(Repository, repository_id)
    if not repo:
        raise HTTPException(404, "仓库不存在")
    if repo.snapshot.get("source") != ("demo" if settings.devflow_mode == "demo" else "github"):
        raise HTTPException(409, "仓库数据来源与当前运行模式不同，请切换配置并重启。")
    return repo


def repo_summary(repo):
    return {"id": repo.id, "full_name": repo.full_name, "synced_at": repo.synced_at, "source": repo.snapshot.get("source")}


@app.get("/api/health")
def health():
    return {"status": "ok", "version":app.version, "stage":5, "mode": settings.devflow_mode, "model": "演示规则引擎（未调用大模型）" if settings.devflow_mode == "demo" else settings.llm_model or "尚未配置", "storage": "sqlite" if settings.database_url.startswith("sqlite") else "postgresql", "retrieval": ("milvus-lite + bm25" if settings.milvus_deployment=="lite" else "milvus + bm25") if settings.retrieval_backend == "milvus" else "bm25", "external_writes": settings.devflow_mode == "live" and settings.auth_enabled and settings.github_write_enabled and bool(settings.github_token.get_secret_value())}


@app.get("/api/repositories")
def repositories(request: Request, db: Session = Depends(get_db)):
    source = "demo" if settings.devflow_mode == "demo" else "github"
    allowed = accessible_repositories(db,request.state.actor)
    return [repo_summary(x) for x in db.scalars(select(Repository)) if x.snapshot.get("source") == source and (allowed is None or x.id in allowed)]


async def fetch_snapshot(name, previous=None):
    if settings.devflow_mode == "demo":
        if name != DEMO_REPO:
            raise HTTPException(400, "演示模式只提供 demo/devflow-shop；连接真实仓库请配置 live 模式。")
        return demo_snapshot()
    github = GitHubClient(settings, (previous or {}).get("_http_cache"))
    try:
        return await github.snapshot(name,previous)
    except (GitHubError, ValueError) as exc:
        raise HTTPException(502, safe_error(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, "仓库同步失败，请核对网络和配置。") from exc
    finally:
        await github.close()


@app.post("/api/repositories")
async def add_repository(body: RepositoryInput, db: Session = Depends(get_db)):
    from sqlalchemy import func
    existing=db.scalar(select(Repository).where(func.lower(Repository.full_name)==body.full_name.lower()))
    if existing:return await sync_repository(existing.id,db)
    snapshot = await fetch_snapshot(body.full_name)
    # Use canonical GitHub name to avoid case-variant duplicates.
    repo = db.scalar(select(Repository).where(Repository.full_name == snapshot["name"]))
    if repo:return await sync_repository(repo.id,db)
    if not repo:
        repo = Repository(full_name=snapshot["name"])
        db.add(repo)
    repo.snapshot, repo.synced_at = snapshot, utcnow()
    db.flush()
    sync_snapshot_documents(db, repo, settings)
    db.commit()
    return repo_summary(repo)


@app.post("/api/repositories/{repository_id}/sync")
async def sync_repository(repository_id: str, db: Session = Depends(get_db)):
    repo_or_404(db,repository_id)
    try:await manual_refresh(settings,repository_id,fetch_snapshot)
    except SyncError as exc:raise HTTPException(409,str(exc)) from exc
    except HTTPException:raise
    except Exception as exc:raise HTTPException(502,"仓库同步失败，原快照保留，请检查连接后重试。") from exc
    db.expire_all()
    return repo_summary(db.get(Repository,repository_id))


@app.get("/api/repositories/{repository_id}/snapshot")
def snapshot(repository_id: str, db: Session = Depends(get_db)):
    repo = repo_or_404(db, repository_id)
    return {**{k: v for k, v in repo.snapshot.items() if not k.startswith("_")}, "synced_at": repo.synced_at}


@app.get("/api/repositories/{repository_id}/task-skills")
def task_skills(repository_id: str, db: Session = Depends(get_db)):
    repo_or_404(db, repository_id)
    try:
        skills = skill_catalog()
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc
    reject_credentials(json.dumps(skills, ensure_ascii=False), settings)
    return {"skills": skills, "scope": "服务端固定版本的只读分析流程；检查项不代表已完成。演示模式仍使用规则引擎。"}


@app.get("/api/repositories/{repository_id}/code/status")
def code_status(repository_id: str, db: Session = Depends(get_db)):
    repo_or_404(db, repository_id)
    return WorkspaceManager(settings).status(repository_id)


@app.post("/api/repositories/{repository_id}/code/sync")
async def sync_code(repository_id: str, body: WorkspaceSyncInput, db: Session = Depends(get_db)):
    repo = repo_or_404(db, repository_id)
    if body.source == "github" and repo.snapshot["source"] == "demo":
        raise HTTPException(409, "演示仓库没有对应的远程 Git 仓库。")
    try:
        return await WorkspaceManager(settings).sync(repository_id, repo.full_name, body.source, repo.snapshot)
    except (WorkspaceError, GitHubError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/repositories/{repository_id}/code/files")
async def code_files(repository_id: str, db: Session = Depends(get_db)):
    repo_or_404(db, repository_id)
    manager = WorkspaceManager(settings)
    try:
        return await asyncio.to_thread(manager.files, manager.ref(repository_id))
    except WorkspaceError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/repositories/{repository_id}/code/file")
async def read_code(repository_id: str, path: str = Query(max_length=500), line_start: int = Query(default=1, ge=1), line_end: int = Query(default=200, ge=1), revision: str | None = Query(default=None, pattern=r"^[0-9a-f]{40}$"), source: str | None = Query(default=None, pattern=r"^(github|local_project)$"), db: Session = Depends(get_db)):
    repo = repo_or_404(db, repository_id)
    manager = WorkspaceManager(settings)
    ref = manager.ref(repository_id)
    if ref and revision:
        ref = {**ref, "sha": revision, "source": source or ref["source"]}
    try:
        hit = await asyncio.to_thread(manager.read, ref, path, line_start, line_end)
        return code_link(hit, repo.full_name)
    except WorkspaceError as exc:
        raise HTTPException(409, str(exc)) from exc


def code_link(hit, repo_name):
    if hit["source"] == "github":
        from urllib.parse import quote
        hit["url"] = f"https://github.com/{repo_name}/blob/{hit['sha']}/{quote(hit['path'], safe='/')}#L{hit['line_start']}-L{hit['line_end']}"
    return hit


@app.post("/api/repositories/{repository_id}/code/search")
async def search_code(repository_id: str, body: CodeSearchInput, db: Session = Depends(get_db)):
    repo = repo_or_404(db, repository_id)
    manager = WorkspaceManager(settings)
    try:
        found = await asyncio.to_thread(manager.search, manager.ref(repository_id), body.query, body.path_prefix, body.top_k)
        found["results"] = [code_link(x, repo.full_name) for x in found["results"]]
        return found
    except WorkspaceError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/repositories/{repository_id}/knowledge/status")
def knowledge_status(repository_id: str, db: Session = Depends(get_db)):
    repo_or_404(db, repository_id)
    return status_for(db, repository_id, settings)


@app.get("/api/repositories/{repository_id}/knowledge/services")
async def knowledge_services(repository_id: str, db: Session = Depends(get_db)):
    repo_or_404(db,repository_id)
    return await service_status(settings,status_for(db,repository_id,settings)["index"]["dimension"])


@app.get("/api/repositories/{repository_id}/knowledge/documents")
def knowledge_documents(repository_id: str, db: Session = Depends(get_db)):
    repo_or_404(db, repository_id)
    chunks = load_corpus(db, repository_id)
    return [{"id": doc.id, "title": doc.title, "path": doc.path, "content": doc.content, "source": doc.source, "url": doc.url, "revision": doc.revision, "updated_at": doc.updated_at, "chunk_count": sum(x["document_id"] == doc.id for x in chunks)} for doc in db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.repository_id == repository_id, KnowledgeDocument.is_deleted.is_(False)).order_by(KnowledgeDocument.path))]


@app.post("/api/repositories/{repository_id}/knowledge/documents")
def import_document(repository_id: str, body: DocumentInput, db: Session = Depends(get_db)):
    repo_or_404(db, repository_id)
    data = {**body.model_dump(), "id": body.path, "url": str(body.url) if body.url else "", "revision": digest(body.content)}
    doc, changed = upsert_document(db, repository_id, data, settings)
    db.commit()
    return {"id": doc.id, "changed": changed, "status": status_for(db, repository_id, settings)}


@app.post("/api/repositories/{repository_id}/knowledge/documents/{document_id}/archive")
def archive_document(repository_id: str, document_id: str, db: Session = Depends(get_db)):
    repo_or_404(db, repository_id)
    doc = db.get(KnowledgeDocument, document_id)
    if not doc or doc.repository_id != repository_id:
        raise HTTPException(404, "文档不存在")
    if doc.source != "manual":
        raise HTTPException(409, "同步文档由 GitHub 快照管理；可在源仓库修改后重新同步。")
    doc.is_deleted, doc.updated_at = True, utcnow()
    db.commit()
    return status_for(db, repository_id, settings)


@app.post("/api/repositories/{repository_id}/knowledge/search")
async def knowledge_search(repository_id: str, body: SearchInput, db: Session = Depends(get_db)):
    repo_or_404(db, repository_id)
    try:
        return await search(repository_id, body.query, settings, body.mode, body.top_k)
    except RetrievalError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/repositories/{repository_id}/knowledge/index")
def index_knowledge(repository_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    repo_or_404(db, repository_id)
    try:
        generation, started = begin_index(db, repository_id, settings)
    except RetrievalError as exc:
        raise HTTPException(409, str(exc)) from exc
    if started:
        background_tasks.add_task(build_index, repository_id, generation, settings)
    return {"started": started, "generation": generation, "status": status_for(db, repository_id, settings)}


@app.get("/api/repositories/{repository_id}/runs")
def runs(repository_id: str, db: Session = Depends(get_db)):
    repo_or_404(db, repository_id)
    expire_jobs(db)
    records = db.scalars(select(AgentRun).where(AgentRun.repository_id == repository_id).order_by(AgentRun.created_at.desc()).limit(30))
    return [{"id": x.id, "question": x.question, "task": x.task, "status": x.status, "created_at": x.created_at, "conversation_id": x.conversation_id, "mode": x.mode, "result": x.result, "recovery": recovery_info(db.get(RunCheckpoint, x.id), x, settings)} for x in records]


@app.get("/api/repositories/{repository_id}/runs/{run_id}")
def run_detail(repository_id: str, run_id: str, db: Session = Depends(get_db)):
    repo_or_404(db, repository_id)
    expire_jobs(db)
    run = db.get(AgentRun, run_id)
    if not run or run.repository_id != repository_id:
        raise HTTPException(404, "运行记录不存在")
    job = db.get(RunJob, run.id)
    return {"id": run.id, "question": run.question, "task": run.task, "status": run.status, "events": run.events, "result": run.result, "conversation_id": run.conversation_id, "background": bool(job), "stop_requested": bool(job and job.stop_requested), "recovery": recovery_info(db.get(RunCheckpoint, run.id), run, settings)}


@app.get("/api/repositories/{repository_id}/runs/{run_id}/fact-review")
def review_saved_run(repository_id: str, run_id: str, db: Session = Depends(get_db)):
    repo_or_404(db, repository_id)
    run = db.get(AgentRun, run_id)
    if not run or run.repository_id != repository_id:
        raise HTTPException(404, "运行记录不存在")
    if run.status != "completed" or not run.result:
        raise HTTPException(409, "只有已完成的分析可以复核。")
    reviewed = apply_review(run.result, {e["id"]: e for e in run.result.get("evidence", [])})
    if reviewed.get("fact_review"):
        reviewed["fact_review"]["preview"] = True
    return {"run_id": run.id, "persisted": False, "reviewed_analysis": reviewed}


@app.post("/api/repositories/{repository_id}/runs/{run_id}/stop")
async def stop_run(repository_id: str, run_id: str, db: Session = Depends(get_db)):
    repo_or_404(db, repository_id)
    run = db.get(AgentRun, run_id)
    if not run or run.repository_id != repository_id: raise HTTPException(404, "运行记录不存在")
    if request_stop(db, run):
        db.expire_all()
        return run_detail(repository_id, run_id, db)
    worker = ACTIVE_RUNS.get(run_id)
    if worker and not worker.done():
        if not worker.cancelling(): worker.cancel()
        with contextlib.suppress(asyncio.CancelledError): await worker
    elif run.status == "running":
        raise HTTPException(409, "当前进程没有该运行的执行任务，请刷新运行记录。")
    db.expire_all()
    return run_detail(repository_id, run_id, db)


@app.post("/api/repositories/{repository_id}/runs/{run_id}/resume")
async def resume_run(repository_id: str, run_id: str, db: Session = Depends(get_db)):
    repo_or_404(db, repository_id)
    run = db.get(AgentRun, run_id)
    if not run or run.repository_id != repository_id: raise HTTPException(404, "运行记录不存在")
    if db.get(RunJob, run_id): raise HTTPException(409, "后台任务请使用从断点继续的后台恢复接口。")
    checkpoint = db.get(RunCheckpoint, run_id)
    worker = ACTIVE_RUNS.get(run_id)
    if worker and not worker.done(): raise HTTPException(409, "该运行正在执行或停止中，不能重复恢复。")
    try:
        info = recovery_info(checkpoint, run, settings)
        if not info or not info["available"]:
            raise CheckpointError(info["reason"] if info else "该记录未保存恢复点，请重新发起协作检查。")
        state = validate_checkpoint(checkpoint, run)
        await validate_sources(state, settings)
        token = claim_resume(db, run, checkpoint, settings)
        return stream_run(run_id, settings, token=token, resumed=True)
    except (CheckpointError, WorkspaceError, GitHubError) as exc:
        raise HTTPException(409, safe_error(exc)) from exc


@app.get("/api/queue/status")
def background_status(request: Request, db: Session = Depends(get_db)):
    return queue_status(db, settings, accessible_repositories(db,request.state.actor))


@app.get("/api/repositories/{repository_id}/runs/{run_id}/events")
def subscribe_run(repository_id: str, run_id: str, after: int = Query(default=0, ge=0), db: Session = Depends(get_db)):
    detail = run_detail(repository_id, run_id, db)
    last = detail["events"][-1]["sequence"] if detail["events"] else 0
    if after > last: raise HTTPException(422, "事件游标超过当前运行记录。")
    return event_stream(run_id, after, settings.queue_poll_seconds)


@app.post("/api/repositories/{repository_id}/runs/{run_id}/resume-background", status_code=202)
async def queue_resume(repository_id: str, run_id: str, db: Session = Depends(get_db)):
    repo_or_404(db, repository_id)
    expire_jobs(db)
    run = db.get(AgentRun, run_id)
    if not run or run.repository_id != repository_id: raise HTTPException(404, "运行记录不存在")
    cp = db.get(RunCheckpoint, run_id)
    try:
        info = recovery_info(cp, run, settings)
        if not info or not info["available"]:
            raise CheckpointError(info["reason"] if info else "该记录没有恢复点，请重新分析。")
        state = validate_checkpoint(cp, run)
        await validate_sources(state, settings)
        claim_resume(db, run, cp, settings, status="queued", commit=False)
        enqueue(db, run, settings, state["input"], resumed=True)
        db.commit()
        return {"run_id": run.id, "conversation_id": run.conversation_id, "status": "queued"}
    except (CheckpointError, WorkspaceError, GitHubError) as exc:
        db.rollback()
        raise HTTPException(409, safe_error(exc)) from exc


@app.get("/api/repositories/{repository_id}/drafts")
def drafts(repository_id: str, db: Session = Depends(get_db)):
    repo_or_404(db, repository_id)
    return [draft_json(db,x) for x in db.scalars(select(ActionDraft).where(ActionDraft.repository_id == repository_id).order_by(ActionDraft.created_at.desc()).limit(30))]


@app.post("/api/repositories/{repository_id}/drafts")
def create_draft(repository_id: str, body: DraftInput, request: Request, db: Session = Depends(get_db)):
    repo_or_404(db, repository_id)
    run = db.get(AgentRun, body.run_id)
    if not run or run.repository_id != repository_id or run.status != "completed" or not run.result:
        raise HTTPException(400, "只能从当前仓库已完成的分析生成草稿")
    existing = db.scalar(select(ActionDraft).where(ActionDraft.run_id == run.id))
    if existing:
        return {"id": existing.id, "body": existing.body, "status": existing.status}
    result = apply_review(run.result, {e["id"]: e for e in run.result.get("evidence", [])})
    if result.get("fact_review", {}).get("status") == "conflict":
        raise HTTPException(409, "分析包含与源码冲突的数值或公式关系，请先在事实核对区复查；本次未生成评论草稿。")
    content = f"## {result['title']}\n\n{result['summary']}\n\n建议：{result['recommendation']}\n\n" + "\n".join(f"- {x['title']}：{x['detail']}" for x in result["findings"])
    content += "\n\n证据缺口：\n" + "\n".join(f"- {x}" for x in result["gaps"])
    content += "\n\n分析依据：\n" + "\n".join(f"- {x['id']}：{x.get('citation') or x['title']}（来源：{x['source']}）" + (f"（SHA {x['sha']}）" if x.get("sha") else "") + (f" {x['url']}" if x.get("url") else "") for x in result["evidence"])
    if run.mode == "demo":
        content += "\n\n本草稿来自演示样例与规则引擎，未调用大模型，不对应真实仓库。"
    draft = ActionDraft(repository_id=repository_id, run_id=run.id, body=content)
    db.add(draft)
    db.flush()
    audit(db,request.state.actor,"draft.create",draft.id,repository_id,run_id=run.id)
    db.commit()
    return {"id": draft.id, "body": draft.body, "status": "draft"}


@app.post("/api/chat/stream")
async def chat(body: ChatInput, request: Request, db: Session = Depends(get_db)):
    return await create_run(body, db, background=False, actor=request.state.actor)


@app.post("/api/chat/runs", status_code=202)
async def submit_run(body: ChatInput, request: Request, db: Session = Depends(get_db)):
    return await create_run(body, db, background=True, actor=request.state.actor)


async def create_run(body, db, background, actor=None):
    if not body.message.strip():
        raise HTTPException(422, "问题不能为空")
    repo = repo_or_404(db, body.repository_id)
    try:
        validate_selection(body.skill_id, body.task, body.target)
        if body.skill_id:
            reject_credentials(json.dumps(skill_catalog(), ensure_ascii=False), settings)
        run_settings = settings.model_copy(update={"analysis_prompt_id": body.prompt_id, "analysis_skill_id": body.skill_id})
        binding = freeze_binding(run_settings)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.conversation_id:
        conversation = db.get(Conversation, body.conversation_id)
        if not conversation or conversation.repository_id != repo.id:
            raise HTTPException(404, "会话不存在或不属于当前仓库")
    else:
        conversation = Conversation(repository_id=repo.id, title=body.message[:100])
        db.add(conversation)
        db.flush()
    previous = list(db.scalars(select(AgentRun).where(AgentRun.conversation_id == conversation.id, AgentRun.status == "completed").order_by(AgentRun.created_at.desc()).limit(3)))
    history = [{"question": x.question, "summary": x.result["summary"]} for x in reversed(previous) if x.result]
    run = AgentRun(repository_id=repo.id, conversation_id=conversation.id, question=body.message, task=body.task, mode=settings.devflow_mode)
    db.add(run)
    db.flush()
    repo_snapshot = {**repo.snapshot, "synced_at": repo.synced_at.isoformat() if repo.synced_at else None}
    token = None
    memories = memory_corpus(db,repo.id)
    if body.task == "workflow":
        try:
            checkpoint = create_checkpoint(db, run, repo_snapshot, WorkspaceManager(settings).ref(repo.id), load_corpus(db, repo.id), body.target, history, run_settings, memories)
            token = checkpoint.lease_token
        except CheckpointError as exc:
            db.rollback()
            raise HTTPException(422, str(exc)) from exc
    frozen = checkpoint.state["input"] if token else {"snapshot": {k:v for k,v in repo_snapshot.items() if not k.startswith("_")}, "workspace": WorkspaceManager(settings).ref(repo.id), "corpus": load_corpus(db, repo.id), "target": body.target, "history": history, "memories":memories}
    frozen["analysis_binding"] = binding
    if background:
        try:
            enqueue(db, run, settings, frozen)
        except CheckpointError as exc:
            db.rollback()
            raise HTTPException(422, str(exc)) from exc
    if actor: audit(db,actor,"run.submit",run.id,repo.id,task=body.task,background=background)
    db.commit()
    if background:
        return {"run_id": run.id, "conversation_id": conversation.id, "status": "queued"}
    return stream_run(run.id, settings, token=token, snapshot=repo_snapshot, target=body.target, history=history, frozen_input=frozen)
