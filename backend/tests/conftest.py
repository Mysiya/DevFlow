import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

backend_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_dir))
os.environ["DEVFLOW_MODE"] = "demo"
os.environ["LLM_API_KEY"] = ""
# Keep API/child Worker checkpoint identities equal even when pytest starts in
# backend (which has a live .env) and a subprocess starts in the project root.
os.environ["LLM_BASE_URL"] = "https://api.example.com/v1"
os.environ["LLM_MODEL"] = ""
os.environ["LLM_REASONING_EFFORT"] = "none"
os.environ["GITHUB_TOKEN"] = ""
os.environ["RETRIEVAL_BACKEND"] = "keyword"
os.environ["EMBEDDING_API_KEY"] = ""
os.environ["RERANK_ENABLED"] = "false"
os.environ["DATABASE_URL"] = "sqlite:///" + (backend_dir / "data" / f"test-{uuid4().hex}.db").as_posix()

from fastapi.testclient import TestClient
from app.db import Base, engine
from app.main import app


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    with TestClient(app) as test_client:
        yield test_client


def pytest_sessionfinish(session, exitstatus):
    engine.dispose()
