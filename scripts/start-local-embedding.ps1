$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRoot '.vector-venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $taskPython)) { throw 'Install backend/requirements-vector.lock.txt in .vector-venv first.' }
& $taskPython (Join-Path $PSScriptRoot 'local-embedding.py')
