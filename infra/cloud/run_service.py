"""Run the web UI, API and independent Worker on one persistent cloud machine."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]


def cloud_environment(values):
    env = dict(values)
    for name in ("AUTH_ENABLED", "AUTH_COOKIE_SECURE"):
        if env.get(name, "true").lower() != "true":
            raise ValueError("Public hosting requires login and Secure cookies")
        env[name] = "true"
    if len(env.get("BOOTSTRAP_ADMIN_PASSWORD", "")) < 12:
        raise ValueError("Set an administrator password with at least 12 characters")
    origins = env.get("CORS_ORIGINS", "").split(",")
    for value in origins:
        origin = urlsplit(value)
        if (origin.scheme != "https" or not origin.hostname or origin.username or origin.password
                or origin.path not in ("", "/") or origin.query or origin.fragment):
            raise ValueError("Set CORS_ORIGINS to the exact public HTTPS origin")
    if env.get("GITHUB_WRITE_ENABLED", "false").lower() != "false":
        raise ValueError("This deployment keeps application GitHub publishing disabled")
    port = int(env.get("PORT", "8080"))
    if not 1024 <= port <= 65535 or port == 8000:
        raise ValueError("PORT must be an available public port above 1023, different from 8000")
    data = Path(env.get("DEVFLOW_DATA_DIR", str(ROOT / "data"))).resolve()
    env.setdefault("DATABASE_URL", "sqlite:///" + (data / "devflow.db").as_posix())
    env.setdefault("WORKSPACE_ROOT", str(data / "code"))
    env.setdefault("RETRIEVAL_BACKEND", "keyword")
    env.setdefault("LOCAL_PROJECT_ENABLED", "false")
    env["GITHUB_WRITE_ENABLED"] = "false"
    env["DEVFLOW_API_URL"] = "http://127.0.0.1:8000"
    env["NEXT_TELEMETRY_DISABLED"] = "1"
    env["PORT"] = str(port)
    return env, data


def stop_children(children):
    for child in reversed(children):
        if child.poll() is None:
            child.terminate()
    deadline = time.monotonic() + 15
    for child in reversed(children):
        try:
            child.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def serve(env, data):
    data.mkdir(parents=True, exist_ok=True)
    children = []
    stopped = False

    def shutdown(*_):
        nonlocal stopped
        stopped = True

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, shutdown)
    try:
        api = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                "--port", "8000", "--no-access-log"], cwd=ROOT / "backend", env=env)
        children.append(api)
        ready = False
        for _ in range(120):
            if stopped or api.poll() is not None:
                return 1
            try:
                with urllib.request.urlopen("http://127.0.0.1:8000/api/auth/session", timeout=1) as response:
                    ready = response.status == 200
                if ready:
                    break
            except (OSError, TimeoutError):
                time.sleep(0.5)
        if not ready:
            print("API did not become ready; stopping deployment", flush=True)
            return 1
        children.append(subprocess.Popen([sys.executable, "-m", "app.worker"], cwd=ROOT / "backend", env=env))
        children.append(subprocess.Popen(["node", "node_modules/next/dist/bin/next", "start", "--hostname",
                                          "0.0.0.0", "--port", env["PORT"]], cwd=ROOT / "frontend", env=env))
        print("DevFlow cloud service started; login is required", flush=True)
        while not stopped:
            if any(child.poll() is not None for child in children):
                print("A service exited; stopping the other services for a clean restart", flush=True)
                return 1
            time.sleep(0.5)
        return 0
    finally:
        stop_children(children)


if __name__ == "__main__":
    try:
        environment, directory = cloud_environment(os.environ)
        sys.exit(serve(environment, directory))
    except (ValueError, OSError):
        print("Cloud startup failed: check HTTPS origin, administrator password, port and data permissions", file=sys.stderr)
        sys.exit(1)
