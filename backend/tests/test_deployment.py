import asyncio

import pytest
from pydantic import SecretStr

from app import main
from app.deployment import deployment_metadata


@pytest.mark.parametrize("value", ["", "a" * 39, "a" * 41, "z" * 40,
                                  "a" * 40 + "\n", "sk-private-value", "https://secret.example/"])
def test_missing_or_invalid_host_metadata_is_unknown_without_echoing_values(monkeypatch, value):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", value)
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", value)
    monkeypatch.setenv("LLM_API_KEY", "unrelated-private-key")
    assert deployment_metadata() == {"git_commit_sha": None, "deployment_id": None}


def test_full_host_metadata_is_normalized(monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "A1" * 20)
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "3DFB0AE8-31A5-4D11-AF34-B6E7F542265D")
    assert deployment_metadata() == {"git_commit_sha": "a1" * 20,
                                     "deployment_id": "3dfb0ae8-31a5-4d11-af34-b6e7f542265d"}


def test_deployment_metadata_requires_admin_and_is_never_cached(client, monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "a1" * 20)
    settings = main.settings.model_copy(update={"auth_enabled": True,
        "bootstrap_admin_password": SecretStr("testing-deployment-1234")})
    monkeypatch.setattr(main, "settings", settings)

    async def bootstrap():
        async with main.lifespan(main.app):
            pass

    asyncio.run(bootstrap())
    assert client.get("/api/admin/deployment").status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": "testing-deployment-1234"}).status_code == 200
    response = client.get("/api/admin/deployment")
    assert response.status_code == 200 and response.json()["git_commit_sha"] == "a1" * 20
    assert "no-store" in response.headers["cache-control"]
    assert client.post("/api/admin/users", json={"username": "viewer", "password": "testing-viewer-1234"}).status_code == 201
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": "viewer", "password": "testing-viewer-1234"}).status_code == 200
    assert client.get("/api/admin/deployment").status_code == 403
