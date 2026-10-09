$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $taskPython)) { throw '请先安装项目的 Python 虚拟环境。' }
Push-Location (Join-Path $taskRoot 'backend')
try { & $taskPython -m app.mcp_server } finally { Pop-Location }
