$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRoot '.vector-venv\Scripts\python.exe'
$taskData = Join-Path $taskRoot 'backend\data\milvus-lite'
if (!(Test-Path -LiteralPath $taskPython)) { throw 'Install backend/requirements-vector.lock.txt in .vector-venv first.' }
# One RPC worker serializes writes in the local single-writer engine.
& $taskPython -m milvus_lite server --data-dir $taskData --host 127.0.0.1 --port 19530 --max-workers 1
