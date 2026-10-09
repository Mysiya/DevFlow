$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw 'Missing .venv. Install backend/requirements.lock.txt first.'
}
Push-Location (Join-Path $taskRoot 'backend')
try {
    & $taskPython -m app.worker
} finally {
    Pop-Location
}
