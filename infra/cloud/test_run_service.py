import importlib.util
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location("run_service", Path(__file__).with_name("run_service.py"))
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


def environment():
    return {"CORS_ORIGINS": "https://devflow.example.com", "BOOTSTRAP_ADMIN_PASSWORD": "local-test-password"}


def test_shared_data_and_internal_api(tmp_path):
    values = environment() | {"DEVFLOW_DATA_DIR": str(tmp_path), "PORT": "8081"}
    env, data = service.cloud_environment(values)
    assert data == tmp_path
    assert env["DATABASE_URL"].endswith("/devflow.db") and env["WORKSPACE_ROOT"] == str(tmp_path / "code")
    assert env["AUTH_ENABLED"] == env["AUTH_COOKIE_SECURE"] == "true"
    assert env["DEVFLOW_API_URL"] == "http://127.0.0.1:8000" and env["PORT"] == "8081"
    assert env["LOCAL_PROJECT_ENABLED"] == env["GITHUB_WRITE_ENABLED"] == "false"
    assert "DATABASE_URL" not in values


@pytest.mark.parametrize("overrides", [
    {"AUTH_ENABLED": "false"}, {"AUTH_COOKIE_SECURE": "false"}, {"BOOTSTRAP_ADMIN_PASSWORD": "short"},
    {"CORS_ORIGINS": ""}, {"CORS_ORIGINS": "http://devflow.example.com"},
    {"CORS_ORIGINS": "https://user:password@devflow.example.com"},
    {"CORS_ORIGINS": "https://devflow.example.com/api"}, {"GITHUB_WRITE_ENABLED": "true"},
    {"PORT": "8000"}, {"PORT": "0"}, {"PORT": "invalid"},
])
def test_public_deployment_rejects_insecure_settings(overrides):
    with pytest.raises(ValueError):
        service.cloud_environment(environment() | overrides)


def test_shutdown_terminates_live_children_and_kills_only_timeout():
    ended, live, stuck = Mock(), Mock(), Mock()
    ended.poll.return_value = 0
    live.poll.return_value = stuck.poll.return_value = None
    stuck.wait.side_effect = [subprocess.TimeoutExpired("service", 15), 0]
    service.stop_children([ended, live, stuck])
    ended.terminate.assert_not_called()
    live.terminate.assert_called_once()
    live.kill.assert_not_called()
    stuck.terminate.assert_called_once()
    stuck.kill.assert_called_once()
