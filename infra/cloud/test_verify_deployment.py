"""Exercise verification outcomes with a fake server, no credentials or network."""
import importlib.util
from pathlib import Path
import sys

import httpx
import pytest

spec = importlib.util.spec_from_file_location("verify_deployment", Path(__file__).resolve().parents[2] / "scripts/verify-deployment.py")
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)
SHA = "a1" * 20


def configure(monkeypatch, login_status=200, metadata=None, health_status=200):
    clock = [0]
    monkeypatch.setattr(verify.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(verify.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setenv("DEVFLOW_ADMIN_PASSWORD", "fake-test-password")
    monkeypatch.setattr(sys, "argv", ["verify-deployment", "--url", "https://app.example.test", "--expected-sha", SHA, "--timeout", "1"])
    paths = []

    def handler(request):
        paths.append(request.url.path)
        assert request.url.host == "app.example.test"
        if request.url.path == "/api/auth/login":
            return httpx.Response(login_status, json={})
        if request.url.path == "/api/admin/deployment":
            return httpx.Response(200, json=metadata if metadata is not None else {"git_commit_sha": SHA})
        if request.url.path == "/api/health":
            return httpx.Response(health_status, json={"status": "ok"})
        assert request.url.path == "/api/auth/logout"
        return httpx.Response(200, json={})

    original = httpx.Client
    monkeypatch.setattr(verify.httpx, "Client", lambda **options: original(transport=httpx.MockTransport(handler), **options))
    return paths


def test_verification_needs_matching_server_commit_and_health(monkeypatch, capsys):
    paths = configure(monkeypatch)
    verify.main()
    assert '"verified": true' in capsys.readouterr().out
    assert paths == ["/api/auth/login", "/api/admin/deployment", "/api/health", "/api/auth/logout"]


@pytest.mark.parametrize("metadata", [{"git_commit_sha": "b2" * 20}, {"git_commit_sha": None}, {}])
def test_other_or_missing_commit_never_passes_verification(monkeypatch, capsys, metadata):
    paths = configure(monkeypatch, metadata=metadata)
    with pytest.raises(SystemExit, match="not observed"):
        verify.main()
    assert not capsys.readouterr().out
    assert paths[-1] == "/api/auth/logout" and "/api/health" not in paths


def test_failed_health_never_passes_verification(monkeypatch, capsys):
    configure(monkeypatch, health_status=503)
    with pytest.raises(SystemExit, match="not observed"):
        verify.main()
    assert not capsys.readouterr().out


def test_rejected_login_stops_without_printing_response_body(monkeypatch, capsys):
    paths = configure(monkeypatch, login_status=401)
    with pytest.raises(SystemExit, match="login was rejected"):
        verify.main()
    assert paths == ["/api/auth/login"] and not capsys.readouterr().out
