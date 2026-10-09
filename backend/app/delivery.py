"""Local evaluation records, usage statistics and reviewable weekly reports."""
import asyncio
import hashlib
import json
import math
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .db import AgentRun, EvaluationBatch, KnowledgeDocument, Repository, WeeklyReport, get_db
from .evaluation import evaluate
from .fact_review import apply_review
from .governance import reject_credentials
from .knowledge import digest, upsert_document
from .security import audit, runtime_settings
from .telemetry import run_metrics

router=APIRouter(prefix="/api/repositories/{repository_id}")
ROOT=Path(__file__).resolve().parents[2]


def evaluator_python():
    for path in (ROOT/".eval-venv/Scripts/python.exe",ROOT/".eval-venv/bin/python"):
        if path.is_file():return path
    return None


@router.get("/delivery/status")
def delivery_status(repository_id: str, settings=Depends(runtime_settings)):
    today=datetime.now(timezone(timedelta(hours=8))).date()
    return {"dataset":"core-v4","case_count":60,"ragas_available":evaluator_python() is not None,
            "week_start":(today-timedelta(days=today.weekday())).isoformat(),
            "mcp_enabled":len(settings.mcp_access_token.get_secret_value())>=32 and repository_id in [item.strip() for item in settings.mcp_repository_ids.split(",")],
            "mcp_transport":"stdio","scope":"本地固定样例评测，周报仅汇总已保存分析；未启用外部发布。"}


@router.get("/metrics")
def metrics(repository_id: str, db=Depends(get_db)):
    runs=list(db.scalars(select(AgentRun).where(AgentRun.repository_id==repository_id).order_by(AgentRun.created_at.desc()).limit(30)))
    return {"scope":"最近 30 次运行；历史未记录用量的运行不补算。","runs":[{**run_metrics(db,r),"question":r.question,"task":r.task} for r in runs]}


def batch_json(batch):
    return {"id":batch.id,"dataset":batch.dataset,"fingerprint":batch.fingerprint,"results":batch.results,"created_at":batch.created_at}


@router.get("/evaluations")
def evaluations(repository_id: str, db=Depends(get_db)):
    return [batch_json(b) for b in db.scalars(select(EvaluationBatch).where(EvaluationBatch.repository_id==repository_id).order_by(EvaluationBatch.created_at.desc()).limit(20))]


@router.post("/evaluations",status_code=201)
async def create_evaluation(repository_id: str, request: Request, db=Depends(get_db), settings=Depends(runtime_settings)):
    dataset,fingerprint,results=await asyncio.wait_for(evaluate(settings),timeout=30)
    batch=EvaluationBatch(repository_id=repository_id,dataset=dataset,fingerprint=fingerprint,results=results)
    db.add(batch);db.flush();audit(db,request.state.actor,"evaluation.run",batch.id,repository_id,dataset=dataset,fingerprint=fingerprint)
    db.commit();return batch_json(batch)


def get_batch(db,repo,ident):
    batch=db.get(EvaluationBatch,ident)
    if not batch or batch.repository_id!=repo:raise HTTPException(404,"评测记录不存在。")
    return batch


@router.get("/evaluations/{batch_id}/export")
def export_evaluation(repository_id: str,batch_id: str,db=Depends(get_db)):
    batch=get_batch(db,repository_id,batch_id)
    return {"dataset":batch.dataset,"fingerprint":batch.fingerprint,"samples":batch.results["ragas_samples"]}


@router.post("/evaluations/{batch_id}/ragas")
async def ragas_evaluation(repository_id: str,batch_id: str,request: Request,db=Depends(get_db)):
    batch=get_batch(db,repository_id,batch_id)
    if batch.results.get("ragas"):return batch_json(batch)
    executable=evaluator_python()
    if not executable:raise HTTPException(409,"未安装隔离的 Ragas 评测环境，请按 README 安装。")
    # The evaluator receives fixed fixture contexts only; no model credentials or user repository data.
    env={key:value for key,value in os.environ.items() if not any(word in key.upper() for word in ("KEY","TOKEN","SECRET","PASSWORD","LLM","EMBEDDING","RERANK","TRACING"))}
    env.update(PYTHONUTF8="1",RAGAS_DO_NOT_TRACK="true",HF_HUB_OFFLINE="1",HF_DATASETS_OFFLINE="1",LANGCHAIN_TRACING_V2="false")
    proc=await asyncio.create_subprocess_exec(str(executable),str(ROOT/"scripts/evaluate-ragas.py"),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=env,cwd=str(ROOT))
    try:
        output,errors=await asyncio.wait_for(proc.communicate(json.dumps(batch.results["ragas_samples"],ensure_ascii=False).encode()),timeout=45)
        if proc.returncode:raise HTTPException(502,"Ragas 评测失败，请检查评测环境版本；未调用外部模型。")
        result=json.loads(output)
        if result.get("provider")!="ragas-nonllm" or len(result.get("rows",[]))!=len(batch.results["ragas_samples"]):raise ValueError("结果格式无效")
        for row,sample in zip(result["rows"],batch.results["ragas_samples"]):
            if any(row.get(key)!=sample[key] for key in ("variant","case_id")):raise ValueError("样例标识无效")
            for key in ("context_recall","context_precision"):
                score=row.get(key)
                if type(score) not in (int,float) or not math.isfinite(score) or not 0<=score<=1:raise ValueError("指标无效")
        batch.results={**batch.results,"ragas":result}
        audit(db,request.state.actor,"evaluation.ragas",batch.id,repository_id,version=result["version"])
        db.commit();return batch_json(batch)
    except asyncio.TimeoutError:raise HTTPException(504,"Ragas 评测超时，请检查本地评测环境。") from None
    except ValueError:raise HTTPException(502,"Ragas 返回无效结果。") from None
    finally:
        if proc.returncode is None:
            proc.kill();await proc.wait()


class ReportInput(BaseModel):
    week_start: date


def report_json(report):
    return {"id":report.id,"week_start":report.week_start,"body":report.body,"source_run_ids":report.source_run_ids,
            "created_by":report.created_by,"document_id":report.document_id,"created_at":report.created_at,"fingerprint":report.fingerprint}


@router.get("/reports")
def reports(repository_id: str,db=Depends(get_db)):
    return [report_json(r) for r in db.scalars(select(WeeklyReport).where(WeeklyReport.repository_id==repository_id).order_by(WeeklyReport.created_at.desc()).limit(20))]


@router.post("/reports",status_code=201)
def create_report(repository_id: str,body: ReportInput,request: Request,db=Depends(get_db),settings=Depends(runtime_settings)):
    if body.week_start.weekday()!=0:raise HTTPException(422,"周报开始日期必须是周一（北京时间）。")
    start=datetime.combine(body.week_start,time.min,tzinfo=timezone(timedelta(hours=8)))
    end=start+timedelta(days=7)
    runs=list(db.scalars(select(AgentRun).where(AgentRun.repository_id==repository_id,AgentRun.mode==settings.devflow_mode,AgentRun.status=="completed",AgentRun.created_at>=start.astimezone(timezone.utc),AgentRun.created_at<end.astimezone(timezone.utc)).order_by(AgentRun.created_at,AgentRun.id).limit(101)))
    if len(runs)>100:raise HTTPException(409,"该周超过 100 条分析，请通过离线报表汇总，避免遗漏。")
    name=db.get(Repository,repository_id).full_name
    lines=[f"# {name} · 已保存分析周报",f"\n范围：北京时间 {body.week_start} 至 {(end-timedelta(days=1)).date()}。",
           f"\n来源：本地保存的 {len(runs)} 次已完成分析，模式 {settings.devflow_mode}。不是 GitHub 全量活动周报，也不代表所有结论已获人工确认。",
           "\n每项为当时来源范围内的历史摘要，可能已经过时；核对当前状态应读取最新源码和已批准记忆。摘要、建议、证据与待确认项为有限摘录，完整记录见来源运行。"]
    for run in runs:
        original=run.result or {}
        result=apply_review(original,{e["id"]:e for e in original.get("evidence",[])})
        created=run.created_at if run.created_at.tzinfo else run.created_at.replace(tzinfo=timezone.utc)
        versions=sorted({e["sha"] for e in result.get("evidence",[]) if e.get("sha")})
        lines.extend([f"\n## {result.get('title',run.task)}",f"\n来源运行：{run.id}",f"分析创建时间：{created.astimezone(timezone(timedelta(hours=8))).isoformat()}",
                      "证据版本："+(", ".join(versions) or "未记录版本；请以来源运行的证据范围为准。"),
                      "\n"+result.get("summary","")[:600],"\n建议："+result.get("recommendation","")[:300]])
        lines.extend(f"- 证据：{e['id']} · {e.get('citation') or e.get('title','')}" for e in result.get("evidence",[])[:4])
        if result.get("fact_review",{}).get("status")=="conflict":
            lines.append("- 事实核对：存在源码事实冲突，冲突段落未纳入摘要；原文及版本见来源运行的核对预览。")
        lines.extend("- 待确认："+gap[:300] for gap in result.get("gaps",[])[:3])
    if not runs:lines.append("\n本时间范围没有已完成分析；不能据此判断仓库没有活动。")
    content="\n".join(lines);reject_credentials(content,settings)
    if len(content)>100000:raise HTTPException(409,"周报超过知识文档长度上限，请离线整理。")
    fingerprint=digest(content)
    existing=db.scalar(select(WeeklyReport).where(WeeklyReport.repository_id==repository_id,WeeklyReport.week_start==str(body.week_start),WeeklyReport.fingerprint==fingerprint))
    if existing:return report_json(existing)
    report=WeeklyReport(repository_id=repository_id,week_start=str(body.week_start),fingerprint=fingerprint,body=content,source_run_ids=[r.id for r in runs],created_by=request.state.actor["username"])
    try:
        db.add(report);db.flush();audit(db,request.state.actor,"report.create",report.id,repository_id,runs=len(runs));db.commit()
    except IntegrityError:
        db.rollback();report=db.scalar(select(WeeklyReport).where(WeeklyReport.repository_id==repository_id,WeeklyReport.week_start==str(body.week_start),WeeklyReport.fingerprint==fingerprint))
        if not report:raise
    return report_json(report)


@router.post("/reports/{report_id}/publish-knowledge")
def publish_report(repository_id: str,report_id: str,request: Request,db=Depends(get_db),settings=Depends(runtime_settings)):
    report=db.get(WeeklyReport,report_id)
    if not report or report.repository_id!=repository_id:raise HTTPException(404,"周报不存在。")
    reject_credentials(report.body,settings)
    doc,changed=upsert_document(db,repository_id,{"id":f"weekly:{report.id}","title":f"已保存分析周报 {report.week_start}","path":f"reports/{report.week_start}-{report.fingerprint[:12]}.md","content":report.body,"url":"","revision":digest(report.body)},settings)
    report.document_id=doc.id
    if changed:audit(db,request.state.actor,"report.publish_knowledge",report.id,repository_id,document_id=doc.id)
    db.commit();return report_json(report)
