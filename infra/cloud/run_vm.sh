#!/bin/sh
# Keep this service running on the claimed VM; runtime secrets stay outside /app.
set -eu
task_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
task_runtime=${DEVFLOW_RUNTIME_FILE:-/root/.config/devflow/runtime.env}
task_state=$(dirname -- "$task_runtime")
exec 9> "$task_state/service.lock"
flock -n 9 || exit 0
echo $$ > "$task_state/supervisor.pid"
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
    echo "$task_child" > "$task_state/service.pid"
    wait "$task_child" || true
    task_child=''
    echo 'DevFlow exited; restarting in 5 seconds'
    sleep 5 &
    task_child=$!
    wait "$task_child" || true
    task_child=''
done
