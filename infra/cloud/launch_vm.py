"""Idempotent boot/wake launcher for this application's claimed Railway VM."""
from pathlib import Path
import json
import os
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]


def owned_api(command, cwd, root):
    return (cwd == root / "backend"
            and command == [str(root / ".venv/bin/python"), "-m", "uvicorn", "app.main:app",
                            "--host", "127.0.0.1", "--port", "8000", "--no-access-log"])


def process_api(proc, root):
    try:
        command = (proc / "cmdline").read_bytes().rstrip(b"\0").decode().split("\0")
        return owned_api(command, (proc / "cwd").resolve(), root)
    except (FileNotFoundError, PermissionError, ProcessLookupError, UnicodeDecodeError):
        return False


def launch(root=ROOT):
    # Linux-only launcher; importing predicates for local checks needs no fcntl.
    import fcntl

    runtime = Path(os.environ.get("DEVFLOW_RUNTIME_FILE", "/root/.config/devflow/runtime.env"))
    if runtime.stat().st_mode & 0o777 != 0o600:
        raise ValueError("Runtime configuration must have mode 600")
    state = runtime.parent
    with (state / "startup.lock").open("a") as startup_lock:
        try:
            fcntl.flock(startup_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "starting"}
        with (state / "service.lock").open("a") as service_lock:
            try:
                fcntl.flock(service_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"status": "already_running"}
            # The platform may restore the former standalone API. Stop only the
            # exact project command; its port would prevent the full app start.
            orphaned = [proc for proc in Path("/proc").iterdir()
                        if proc.name.isdigit() and process_api(proc, root)]
            for proc in orphaned:
                if process_api(proc, root):
                    try:
                        os.kill(int(proc.name), signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            for _ in range(40):
                if not any(process_api(proc, root) for proc in orphaned):
                    break
                time.sleep(.5)
            else:
                raise RuntimeError("Restored project API did not stop gracefully")
        # Release the service lock before the child acquires its lifetime lock.
        environment = dict(os.environ, DEVFLOW_RUNTIME_FILE=str(runtime))
        with (state / "service.log").open("ab") as log:
            subprocess.Popen(["sh", "infra/cloud/run_vm.sh"], cwd=root, env=environment,
                             stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                             start_new_session=True, close_fds=True)
        return {"status": "started", "recovered_owned_api_count": len(orphaned)}


if __name__ == "__main__":
    try:
        print(json.dumps(launch()))
    except (OSError, ValueError, RuntimeError):
        print("VM startup failed: check private configuration, project paths and owned process logs")
        raise SystemExit(1)
