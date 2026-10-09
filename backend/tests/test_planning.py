import asyncio
import json

import pytest

from app.agents import AgentService, result
from app.config import Settings
from app.demo import demo_snapshot
from app.llm import ModelClient
from app.planning import Plan, Planner, constrain, execute_plan, observe
from app.tools import Tools


def context():
    return {"issue_numbers": [42], "pr_numbers": [18], "ci_ids": [301], "workspace": None, "document_count": 3, "explicit_ci": None}


@pytest.mark.parametrize("steps", [
    [{"id":"a","agent":"report","title":"x","depends_on":["a"]}],
    [{"id":"a","agent":"report","title":"x","depends_on":["missing"]}],
    [{"id":"a","agent":"report","title":"x","depends_on":["b"]},{"id":"b","agent":"report","title":"y","depends_on":["a"]}],
    [{"id":"a","agent":"report","title":"x"},{"id":"a","agent":"report","title":"y"}],
    [{"id":"a","agent":"delete_repository","title":"x"}],
    [{"id":"results","agent":"report","title":"x"}],
])
def test_plans_reject_invalid_dags_and_unregistered_agents(steps):
    with pytest.raises(ValueError): Plan.model_validate({"reason":"test","steps":steps})


def test_plan_range_and_ci_dependency_are_enforced():
    plan = Plan.model_validate({"reason":"test","steps":[{"id":"pr","agent":"pr","title":"PR","target":18},{"id":"ci","agent":"ci","title":"CI","target":301}]})
    chosen = constrain(plan, context(), 4)
    assert chosen.steps[1].depends_on == ["pr"] and chosen.steps[1].target is None
    with pytest.raises(ValueError): constrain(chosen, context(), 1)
    for item in ({"id":"x","agent":"pr","title":"x","target":999},{"id":"x","agent":"code","title":"x","query":"yield"}):
        with pytest.raises(ValueError): constrain(Plan.model_validate({"reason":"test","steps":[item]}), context(), 4)


def test_ci_can_resolve_current_pr_checks_without_snapshot_runs():
    scope = {**context(), "ci_ids": []}
    plan = Plan.model_validate({"reason":"current PR","steps":[{"id":"pr","agent":"pr","title":"PR","target":18},{"id":"ci","agent":"ci","title":"Current CI"}]})
    chosen = constrain(plan, scope, 4)
    assert chosen.steps[1].depends_on == ["pr"] and chosen.steps[1].target is None
    with pytest.raises(ValueError):
        constrain(Plan.model_validate({"reason":"x","steps":[{"id":"ci","agent":"ci","title":"CI"}]}), scope, 4)


def test_dag_parallel_limit_dependencies_failure_skip_and_timeout():
    async def scenario():
        events, order = [], []
        active, maximum = 0, 0
        gate = asyncio.Event()
        plan = Plan.model_validate({"reason":"test","steps":[{"id":"a","agent":"report","title":"A"},{"id":"b","agent":"knowledge","query":"x","title":"B"},{"id":"c","agent":"report","title":"C","depends_on":["a","b"]}]})
        async def emit(kind,data): events.append((kind,data))
        async def worker(step,state):
            nonlocal active, maximum
            active += 1; maximum = max(maximum, active); order.append("start:"+step.id)
            if step.id in ("a","b"):
                if active == 2: gate.set()
                await gate.wait()
            if step.id == "c":
                assert {state[x]["status"] for x in ("a","b")} == {"completed"}
            active -= 1; order.append("end:"+step.id)
            return {"status":"completed","analysis":result(step.title,"ok","核验"),"evidence":{},"gaps":[]}
        outcomes = await execute_plan(plan,worker,emit,concurrency=2)
        assert maximum == 2 and len(outcomes) == 3
        assert order.index("start:c") > order.index("end:a") and order.index("start:c") > order.index("end:b")
        broken = Plan.model_validate({"reason":"test","steps":[{"id":"fail","agent":"report","title":"x"},{"id":"dependent","agent":"report","title":"y","depends_on":["fail"]},{"id":"independent","agent":"report","title":"z"}]})
        async def broken_worker(step,state):
            if step.id=="fail": raise RuntimeError("fixture failure")
            assert step.id!="dependent"
            return {"status":"completed","analysis":result("ok","ok","核验"),"evidence":{},"gaps":[]}
        outcomes = await execute_plan(broken,broken_worker,emit)
        assert outcomes["fail"]["status"]=="failed" and outcomes["dependent"]["status"]=="skipped"
        assert outcomes["independent"]["status"]=="completed"
        async def slow(step,state): await asyncio.Event().wait()
        one = Plan.model_validate({"reason":"test","steps":[{"id":"slow","agent":"report","title":"slow"}]})
        outcomes = await execute_plan(one,slow,emit,timeout=.01)
        assert outcomes["slow"]["status"]=="failed" and "超时" in outcomes["slow"]["gaps"][0]
    asyncio.run(scenario())


def test_planner_repairs_once_and_observer_marks_source():
    async def scenario():
        calls, events = [], []
        class FakeModel:
            async def chat(self,*args,**kwargs):
                calls.append(args)
                content = {"reason":"fixture","steps":[{"id":"p","agent":"pr","title":"PR","target":999 if len(calls)==1 else 18}]}
                return {"role":"assistant","content":json.dumps(content)}
        async def emit(kind,data): events.append(kind)
        plan = await Planner(FakeModel(),Settings(),emit).create("review",context(),4)
        assert len(calls)==2 and "planner.repair" in events and plan.steps[0].target==18
    asyncio.run(scenario())
    gaps = observe({"code":{"status":"completed","gaps":[],"analysis":None,"evidence":{"code":{"id":"code:local","source":"local_project","sha":"a"*40}}}})
    assert any("本地项目快照" in x for x in gaps)


def test_completed_task_cannot_be_replanned_with_changed_query():
    plan = Plan.model_validate({"reason":"retry","steps":[{"id":"p","agent":"pr","title":"PR","target":18,"query":"different"}]})
    with pytest.raises(ValueError,match="重复"):
        constrain(plan,context(),2,{"old":{"agent":"pr","target":18,"query":"original","status":"completed"}})


def test_ci_normalization_rejects_duplicate_and_completed_pr_checks():
    scope = {**context(), "ci_ids": [301, 302]}
    plan = Plan.model_validate({"reason":"checks", "steps":[{"id":"pr","agent":"pr","title":"PR","target":18},{"id":"ci1","agent":"ci","title":"CI","target":301},{"id":"ci2","agent":"ci","title":"CI","target":302}]})
    with pytest.raises(ValueError, match="重复"):
        constrain(plan, scope, 4)
    previous = {"pr":{"agent":"pr","target":18,"status":"completed"}, "ci":{"agent":"ci","target":None,"actual_target":301,"status":"completed"}}
    recovery = Plan.model_validate({"reason":"retry", "steps":[{"id":"ci","agent":"ci","title":"CI","target":301}]})
    with pytest.raises(ValueError, match="重复"):
        constrain(recovery, scope, 2, previous)


def test_workflow_keeps_successful_results_and_limits_recovery(monkeypatch):
    async def scenario():
        events, plans = [], []
        async def emit(kind,data): events.append((kind,data))
        async def fake_plan(self,messages,tools=None,structured=False):
            plans.append(messages)
            steps = [{"id":"code","agent":"code","query":"yield","title":"Code"},{"id":"report","agent":"report","title":"Report"}] if len(plans)==1 else [{"id":"docs","agent":"knowledge","query":"tenant isolation","title":"Docs"}]
            return {"role":"assistant","content":json.dumps({"reason":"fixture","steps":steps})}
        async def fake_specialist(self,task,target=None):
            if task=="code": raise RuntimeError("fixture code failure")
            ident = "repo:health" if task=="report" else "doc:tenant"
            self.tools.add_evidence(ident,"fixture","fixture evidence",source="manual")
            return result(task,"fixture result","核验")
        async def fake_analyze(self,*args,**kwargs): return result("Synthesis","retained","核验")
        monkeypatch.setattr(ModelClient,"chat",fake_plan)
        monkeypatch.setattr(ModelClient,"analyze",fake_analyze)
        monkeypatch.setattr(AgentService,"specialist",fake_specialist)
        settings = Settings(devflow_mode="live", llm_api_key="test", llm_model="test")
        tools = Tools(settings,demo_snapshot(),emit,workspace_ref={"repository_id":"a"*32,"source":"local_project","sha":"b"*40})
        try:
            answer = await AgentService(tools,"analyze code").run("workflow",None)
        finally: await tools.close()
        workflow = answer["workflow"]
        assert workflow["replan_count"]==1 and len(plans)==2
        assert {x["status"] for x in workflow["outcomes"]}=={"failed","completed"}
        assert len(workflow["outcomes"])==3 and workflow["synthesis_provider"]=="llm"
        assert {x["id"] for x in answer["evidence"]}=={"repo:health","doc:tenant"}
        assert sum(kind=="observer" for kind,_ in events)==1
    asyncio.run(scenario())


def test_dependency_receives_parent_results_and_citable_evidence(monkeypatch):
    async def scenario():
        captured = {}
        async def emit(*args): pass
        async def fake_plan(self, *args, **kwargs):
            return {"role":"assistant", "content":json.dumps({"reason":"compare", "steps":[{"id":"repo","agent":"report","title":"Read repository"},{"id":"compare","agent":"knowledge","title":"Compare with docs","query":"tenant","depends_on":["repo"]}]})}
        async def fake_analyze(self, role, question, evidence, gaps=None):
            captured[role] = (question, dict(evidence))
            return result(role, "parent repository summary" if role == "report" else "comparison", "核验")
        monkeypatch.setattr(ModelClient, "chat", fake_plan)
        monkeypatch.setattr(ModelClient, "analyze", fake_analyze)
        tools = Tools(Settings(devflow_mode="live", llm_api_key="test", llm_model="test"), demo_snapshot(), emit, workspace_ref=None)
        try:
            answer = await AgentService(tools, "Compare repo and docs").run("workflow", None)
        finally:
            await tools.close()
        question, evidence = captured["knowledge"]
        assert "parent repository summary" in question
        assert "repo:health" in evidence
        assert answer["workflow"]["outcomes"][1]["evidence_ids"]
        assert any(x["id"] == "repo:health" for x in answer["evidence"])
    asyncio.run(scenario())
