#!/bin/sh
# Railway invokes this hook at boot/wake; return without blocking its startup.
set -eu
PATH="/root/.local/share/mise/shims:$PATH"
export PATH
cd /app/devflow
setsid -f .venv/bin/python infra/cloud/launch_vm.py </dev/null >>/root/.config/devflow/startup.log 2>&1
