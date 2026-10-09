"""A restored API must be recognized without matching another app's process."""
import importlib.util
from pathlib import Path, PurePosixPath

import pytest

spec = importlib.util.spec_from_file_location("devflow_vm_launcher", Path(__file__).with_name("launch_vm.py"))
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)

ROOT = PurePosixPath("/app/devflow")
COMMAND = ["/app/devflow/.venv/bin/python", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
           "--port", "8000", "--no-access-log"]


def test_accepts_only_owned_api():
    assert launcher.owned_api(COMMAND, ROOT / "backend", ROOT)


@pytest.mark.parametrize("index,value", [(0, "/other/.venv/bin/python"), (3, "other.main:app"),
                                        (5, "0.0.0.0"), (7, "8080")])
def test_rejects_other_processes(index, value):
    command = list(COMMAND)
    command[index] = value
    assert not launcher.owned_api(command, ROOT / "backend", ROOT)


def test_rejects_other_working_directory_and_arguments():
    assert not launcher.owned_api(COMMAND, ROOT, ROOT)
    assert not launcher.owned_api(COMMAND + ["--reload"], ROOT / "backend", ROOT)


def test_process_disappearing_during_probe_is_safe(tmp_path):
    assert not launcher.process_api(tmp_path / "missing", ROOT)
