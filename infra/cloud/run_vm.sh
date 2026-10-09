#!/bin/sh
# Keep this service running on the claimed VM; runtime secrets stay outside /app.
set -eu
task_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
task_runtime=${DEVFLOW_RUNTIME_FILE:-/root/.config/devflow/runtime.env}
set -a
. "$task_runtime"
set +a
cd "$task_root"
task_child=''
task_stop() {
    trap - INT TERM
    if [ -n "$task_child" ]; then
        kill -TERM "$task_child" 2>/dev/null || true
        wait "$task_child" 2>/dev/null || true
    fi
    exit 0
}
trap task_stop INT TERM
while :; do
    .venv/bin/python infra/cloud/run_service.py &
    task_child=$!
    wait "$task_child" || true
    task_child=''
    echo 'DevFlow exited; restarting in 5 seconds'
    sleep 5 &
    task_child=$!
    wait "$task_child" || true
    task_child=''
done
