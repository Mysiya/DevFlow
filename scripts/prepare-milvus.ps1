$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskInfraPath = Join-Path $taskRoot 'infra'
$taskComposePath = Join-Path $taskInfraPath 'compose.milvus.yaml'
New-Item -ItemType Directory -Force -Path $taskInfraPath | Out-Null
$taskSource = 'https://github.com/milvus-io/milvus/releases/download/v3.0.2/milvus-standalone-docker-compose.yaml'
Invoke-WebRequest -UseBasicParsing -Uri $taskSource -OutFile $taskComposePath
$taskYaml = [System.IO.File]::ReadAllText($taskComposePath)
# Bind optional infrastructure ports to loopback for this local project.
foreach ($taskPort in @('9000', '9001', '19530', '9091')) {
    $taskYaml = $taskYaml.Replace(('"{0}:{0}"' -f $taskPort), ('"127.0.0.1:{0}:{0}"' -f $taskPort))
}
[System.IO.File]::WriteAllText($taskComposePath, $taskYaml, [System.Text.UTF8Encoding]::new($false))
Write-Output ('Prepared official Milvus v3.0.2 configuration: ' + $taskComposePath)
Write-Output 'Configuration only. No containers started. Standalone requires separate validation; see start-milvus-lite.ps1 for the tested local route.'
