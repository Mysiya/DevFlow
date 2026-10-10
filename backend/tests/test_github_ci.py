"""CI evidence survives signed-download failures and concurrent run retries."""
import asyncio

import httpx
import pytest

from app.config import Settings
from app.github import GitHubClient
from app.demo import demo_snapshot
from app.tools import Tools


def inspect(monkeypatch, download_mode="ok", body=b"AssertionError: expected isolation", jobs_count=1, total_count=None):
    original_client = httpx.AsyncClient
    api_seen, download_seen = [], []
    jobs = [{"id": 100 + i, "name": f"job-{i}", "status": "completed", "conclusion": "failure",
             "html_url": f"https://github.com/owner/repo/actions/jobs/{100 + i}",
             "steps": [{"number": 3, "name": "Run regression", "status": "completed", "conclusion": "failure"}]} for i in range(jobs_count)]

    def api(request):
        api_seen.append(request)
        path = request.url.path
        if path == "/repos/owner/repo/actions/runs/42":
            return httpx.Response(200, json={"id": 42, "name": "checks", "status": "completed", "conclusion": "failure",
                                           "head_sha": "a" * 40, "html_url": "https://github.com/owner/repo/actions/runs/42",
                                           "created_at": "2026-10-10T00:00:00Z", "run_attempt": 2})
        if path == "/repos/owner/repo/actions/runs/42/attempts/2/jobs":
            assert request.url.params["per_page"] == "100"
            return httpx.Response(200, json={"total_count": total_count or jobs_count, "jobs": jobs})
        if path.endswith("/logs"):
            if download_mode == "api_timeout":
                raise httpx.ReadTimeout("sensitive signed location must not leak", request=request)
            location = "https://logs.example.test/log?signature=private-test-signature"
            if download_mode == "http":
                location = location.replace("https:", "http:")
            if download_mode == "userinfo":
                location = "https://user:secret@logs.example.test/log"
            return httpx.Response(403 if download_mode == "denied" else 302, headers={"location": location})
        raise AssertionError("A retry must never read the latest attempt jobs endpoint")

    def signed(request):
        download_seen.append(request)
        assert "authorization" not in request.headers
        if download_mode == "download_timeout":
            raise httpx.ReadTimeout("private-test-signature", request=request)
        return httpx.Response(403 if download_mode == "expired" else 200, content=body)

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(api if "base_url" in kwargs else signed)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)

    async def scenario():
        client = GitHubClient(Settings(github_token="test-only-token"))
        try:
            return await client.ci("owner/repo", 42)
        finally:
            await client.close()

    return asyncio.run(scenario()), api_seen, download_seen


def test_attempt_is_fixed_and_signed_logs_receive_no_github_credential(monkeypatch):
    result, api, signed = inspect(monkeypatch)
    assert result["run_attempt"] == 2 and result["head_sha"] == "a" * 40
    assert "AssertionError" in result["logs"] and result["log_gaps"] == []
    assert result["jobs"][0]["steps"][0]["name"] == "Run regression"
    assert all(request.headers["authorization"] == "Bearer test-only-token" for request in api)
    assert len(signed) == 1


@pytest.mark.parametrize("mode", ["denied", "http", "userinfo", "expired", "download_timeout", "api_timeout"])
def test_log_failure_preserves_job_and_step_evidence_without_signed_url(monkeypatch, mode):
    result, _, signed = inspect(monkeypatch, mode)
    assert result["logs"] == "" and result["log_gaps"]
    assert result["jobs"][0]["id"] == 100 and result["jobs"][0]["steps"][0]["conclusion"] == "failure"
    text = " ".join(result["log_gaps"])
    assert "private-test-signature" not in text and "user:secret" not in text
    if mode in ("denied", "http", "userinfo", "api_timeout"):
        assert signed == []


def test_log_and_job_coverage_limits_are_explicit(monkeypatch):
    result, api, signed = inspect(monkeypatch, body=b"x" * 520000, jobs_count=4, total_count=101)
    assert len(result["jobs"]) == 4 and len(signed) == 3
    assert not any("/103/logs" in str(request.url) for request in api)
    assert len(result["logs"]) == 25000
    gaps = " ".join(result["log_gaps"])
    assert "100" in gaps and "前三个" in gaps and "512 KB" in gaps and "25000" in gaps


def test_saved_ci_evidence_keeps_attempt_and_truncation_notice(monkeypatch):
    value = {"id": 42, "name": "checks", "head_sha": "a" * 40, "run_attempt": 2,
             "url": "https://github.com/owner/repo/actions/runs/42", "jobs": [], "logs": "x" * 25000, "log_gaps": []}
    monkeypatch.setattr('app.tools.demo_ci', lambda run_id: value)

    async def emit(*args):
        pass

    async def scenario():
        tools = Tools(Settings(devflow_mode="demo", _env_file=None), demo_snapshot(), emit)
        try:
            returned = await tools.call("inspect_ci", {"run_id": 42})
            evidence = tools.evidence["ci:42"]
            assert returned["log_gaps"] and evidence["run_attempt"] == 2
            assert "Run attempt: 2" in evidence["content"] and "15000" in evidence["content"]
            assert len(evidence["content"]) <= 16000 and evidence["sha"] == "a" * 40
        finally:
            await tools.close()

    asyncio.run(scenario())
