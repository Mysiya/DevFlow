"""Persist request metadata and provider usage; never store prompts or credentials."""
from contextvars import ContextVar
from decimal import Decimal
from sqlalchemy import select
from .db import AgentRun, ModelCall, RunAttempt, SessionLocal, new_id, utcnow

attempt_context = ContextVar("devflow_attempt", default=None)


def start_attempt(run_id):
    with SessionLocal() as db:
        previous=db.scalar(select(RunAttempt.id).where(RunAttempt.run_id==run_id).limit(1))
        old=db.get(AgentRun,run_id)
        prefix=not previous and any(e["type"] in ("run.started","run.resumed","tool.started") for e in old.events or [])
        attempt = RunAttempt(id=new_id(), run_id=run_id,historical_prefix=prefix)
        db.add(attempt); db.commit()
        return attempt.id


def end_attempt(ident, status, elapsed_ms):
    with SessionLocal() as db:
        attempt = db.get(RunAttempt, ident)
        attempt.status, attempt.elapsed_ms, attempt.finished_at = status, elapsed_ms, utcnow()
        db.commit()


def begin_call(settings):
    attempt = attempt_context.get()
    if not attempt: return None
    with SessionLocal() as db:
        call = ModelCall(id=new_id(), attempt_id=attempt, model=settings.llm_model[:200], prices={
            "input":settings.llm_input_price_per_million,"output":settings.llm_output_price_per_million,
            "cached_input":settings.llm_cached_input_price_per_million,"currency":settings.llm_price_currency})
        db.add(call); db.commit()
        return call.id


def provider_usage(raw):
    raw = raw if isinstance(raw, dict) else {}
    def number(value): return value if type(value) is int and 0 <= value <= 10**9 else None
    details = raw.get("prompt_tokens_details")
    cached = raw.get("prompt_cache_hit_tokens", (details or {}).get("cached_tokens", 0) if isinstance(details, dict) else 0)
    usage = {"input":number(raw.get("prompt_tokens")),"output":number(raw.get("completion_tokens")),"cached_input":number(cached)}
    if usage["input"] is not None and usage["cached_input"] is not None and usage["cached_input"] > usage["input"]:
        usage["cached_input"] = None
    return usage


def estimate(usage, prices):
    if any(usage.get(k) is None for k in ("input","output","cached_input")): return None
    if prices.get("input") is None or prices.get("output") is None: return None
    cached = usage["cached_input"]
    if cached and prices.get("cached_input") is None: return None
    value = (Decimal(usage["input"]-cached)*Decimal(str(prices["input"])) +
             Decimal(usage["output"])*Decimal(str(prices["output"])) +
             Decimal(cached)*Decimal(str(prices.get("cached_input") or 0))) / Decimal(1000000)
    return str(value.quantize(Decimal("0.00000001")))


def end_call(ident, status, elapsed_ms, raw=None):
    if not ident: return
    with SessionLocal() as db:
        call=db.get(ModelCall, ident)
        call.status,call.elapsed_ms,call.usage=status,elapsed_ms,provider_usage(raw)
        call.estimated_cost=estimate(call.usage,call.prices)
        db.commit()


def run_metrics(db, run):
    attempts=list(db.scalars(select(RunAttempt).where(RunAttempt.run_id==run.id).order_by(RunAttempt.started_at)))
    calls=list(db.scalars(select(ModelCall).join(RunAttempt).where(RunAttempt.run_id==run.id)))
    complete=bool(attempts) and all(a.finished_at is not None for a in attempts)
    known=all(c.usage.get("input") is not None and c.usage.get("output") is not None for c in calls)
    prefix=any(a.historical_prefix for a in attempts)
    priced=complete and known and not prefix and all(c.estimated_cost is not None for c in calls)
    currencies={c.prices["currency"] for c in calls}
    cost=sum((Decimal(c.estimated_cost) for c in calls if c.estimated_cost is not None),Decimal(0))
    return {"run_id":run.id,"status":run.status,"mode":run.mode,"attempts":len(attempts),"model_calls":len(calls),
            "measured":bool(attempts),"complete":complete,"historical_prefix":prefix,"usage_complete":bool(attempts) and known and not prefix,
            "elapsed_ms":sum(a.elapsed_ms or 0 for a in attempts) if complete else None,
            "known_input_tokens":sum(c.usage.get("input") or 0 for c in calls),
            "known_output_tokens":sum(c.usage.get("output") or 0 for c in calls),
            "unknown_usage_calls":sum(c.usage.get("input") is None or c.usage.get("output") is None for c in calls),
            "estimated_cost":str(cost) if priced and len(currencies)<=1 else None,
            "currency":next(iter(currencies)) if len(currencies)==1 else None,
            "calls":[{"id":c.id,"model":c.model,"status":c.status,"elapsed_ms":c.elapsed_ms,"usage":c.usage,
                      "estimated_cost":c.estimated_cost,"currency":c.prices["currency"]} for c in calls]}
