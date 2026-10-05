param(
  [switch]$NoRestart,
  [switch]$FrontendOnly,
  [switch]$BackendOnly,
  [switch]$ForceBuild,
  [switch]$EnableOpenAI,
  [int]$PostgresPort = 5433
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Backend = Join-Path $Root "backend"
$Logs = Join-Path $Root "logs"
$FrontendEnv = Join-Path $Root ".env.local"
$StopScript = Join-Path $PSScriptRoot "stop-cfs-local.ps1"
$DataCheck = Join-Path $PSScriptRoot "check_cfs_local_data.py"
$CachePrewarm = Join-Path $PSScriptRoot "prewarm_cfs_local.py"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$Python = if ($env:CFS_PYTHON) {
  $env:CFS_PYTHON
} elseif (Test-Path -LiteralPath $VenvPython) {
  $VenvPython
} else {
  "python"
}
$Node = (Get-Command node -ErrorAction SilentlyContinue).Source
$FrontendServer = Join-Path $Root ".next\standalone\server.js"
$BuildMarker = Join-Path $Logs "cfs-presentation-build.json"
$BuildLog = Join-Path $Logs "cfs-presentation-build.log"
$BackendLog = Join-Path $Logs "cfs-presentation-backend.log"
$FrontendLog = Join-Path $Logs "cfs-presentation-frontend.log"
$StartupReport = Join-Path $Logs "local-presentation-startup.json"

$FrontendPort = 3000
$BackendPort = 8000
$PostgresHost = "localhost"
$PostgresDb = "cfs_dev"
$FrontendUrl = "http://127.0.0.1:$FrontendPort"
$ApiBaseUrl = "http://127.0.0.1:$BackendPort"
$startedFrontend = $false
$startedBackend = $false
$startedAt = Get-Date

if (($NoRestart -and ($FrontendOnly -or $BackendOnly)) -or
    ($FrontendOnly -and $BackendOnly)) {
  throw "NoRestart, FrontendOnly, and BackendOnly are mutually exclusive."
}

function Write-Cfs {
  param([string]$Message)
  Write-Host "[cfs-present] $Message"
}

function Invoke-Checked {
  param(
    [scriptblock]$Command,
    [string]$FailureMessage
  )
  & $Command
  if ($LASTEXITCODE -ne 0) {
    throw $FailureMessage
  }
}

function Assert-Command {
  param([string]$Name)
  if (!(Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "Required command '$Name' was not found."
  }
}

function Test-TcpPort {
  param(
    [string]$HostName,
    [int]$Port,
    [int]$TimeoutMs = 3000
  )

  $client = [System.Net.Sockets.TcpClient]::new()
  try {
    $task = $client.ConnectAsync($HostName, $Port)
    try {
      return $task.Wait($TimeoutMs) -and $client.Connected
    } catch {
      return $false
    }
  } finally {
    $client.Dispose()
  }
}

function Upsert-EnvLine {
  param(
    [string[]]$Lines,
    [string]$Key,
    [string]$Value
  )

  $found = $false
  $next = foreach ($line in $Lines) {
    if ($line -match "^\s*$([regex]::Escape($Key))=") {
      $found = $true
      "$Key=$Value"
    } else {
      $line
    }
  }
  if (!$found) {
    $next += "$Key=$Value"
  }
  return @($next)
}

function Ensure-FrontendEnv {
  $lines = if (Test-Path -LiteralPath $FrontendEnv) {
    @(Get-Content -LiteralPath $FrontendEnv)
  } else {
    @()
  }
  $next = Upsert-EnvLine -Lines $lines -Key "NEXT_PUBLIC_CFS_DEPLOYMENT_MODE" -Value "live"
  $next = Upsert-EnvLine -Lines $next -Key "NEXT_PUBLIC_CFS_RUNTIME_MODE" -Value "local"
  $next = Upsert-EnvLine -Lines $next -Key "NEXT_PUBLIC_CFS_DATA_PROVIDER" -Value "local_api"
  $next = Upsert-EnvLine -Lines $next -Key "NEXT_PUBLIC_CFS_AUTH_MODE" -Value "local_dev"
  $next = Upsert-EnvLine -Lines $next -Key "NEXT_PUBLIC_CFS_AI_PROVIDER" -Value $(if ($EnableOpenAI) { "openai" } else { "none" })
  $next = Upsert-EnvLine -Lines $next -Key "NEXT_PUBLIC_CFS_ARTIFACT_PROVIDER" -Value "local_file"
  $next = Upsert-EnvLine -Lines $next -Key "NEXT_PUBLIC_CFS_JOB_PROVIDER" -Value "inline"
  $next = Upsert-EnvLine -Lines $next -Key "NEXT_PUBLIC_USE_BACKEND_API" -Value "true"
  $next = Upsert-EnvLine -Lines $next -Key "NEXT_PUBLIC_CFS_API_BASE_URL" -Value $ApiBaseUrl
  $next = Upsert-EnvLine -Lines $next -Key "NEXT_PUBLIC_CFS_ONLINE_BASEMAP" -Value "false"

  if (($lines -join "`n") -ne ($next -join "`n")) {
    [System.IO.File]::WriteAllLines(
      $FrontendEnv,
      $next,
      [System.Text.UTF8Encoding]::new($false)
    )
    Write-Cfs "Updated non-secret local live-mode settings; unrelated values were preserved."
  } else {
    Write-Cfs "Local live-mode settings are already correct."
  }
}

function Get-GitHead {
  $head = & git -C $Root rev-parse HEAD 2>$null
  if ($LASTEXITCODE -ne 0) {
    return "unknown"
  }
  return ([string]$head).Trim()
}

function Test-PresentationBuildCurrent {
  if ($ForceBuild) {
    return $false
  }

  $buildId = Join-Path $Root ".next\BUILD_ID"
  if (!(Test-Path -LiteralPath $buildId) -or !(Test-Path -LiteralPath $BuildMarker)) {
    return $false
  }
  if (!(Test-Path -LiteralPath (Join-Path $Root ".next\standalone\server.js")) -or
      !(Test-Path -LiteralPath (Join-Path $Root ".next\standalone\.next\static")) -or
      !(Test-Path -LiteralPath (Join-Path $Root ".next\standalone\public"))) {
    return $false
  }

  try {
    $marker = Get-Content -Raw -LiteralPath $BuildMarker | ConvertFrom-Json
    if ($marker.build_id -ne (Get-Content -Raw -LiteralPath $buildId).Trim() -or
        $marker.commit -ne (Get-GitHead) -or
        $marker.deployment_mode -ne "live" -or
        $marker.backend_api -ne $true -or
        $marker.api_base_url -ne $ApiBaseUrl -or
        $marker.online_basemap -ne $false) {
      return $false
    }
  } catch {
    return $false
  }

  $inputs = @(
    Get-ChildItem -LiteralPath (Join-Path $Root "src") -File -Recurse
    Get-ChildItem -LiteralPath (Join-Path $Root "public") -File -Recurse
    Get-Item -LiteralPath (Join-Path $Root "package.json")
    Get-Item -LiteralPath (Join-Path $Root "package-lock.json")
    Get-Item -LiteralPath (Join-Path $Root "next.config.ts") -ErrorAction SilentlyContinue
    Get-Item -LiteralPath $FrontendEnv
  )
  $latestInput = ($inputs | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1).LastWriteTimeUtc
  return (Get-Item -LiteralPath $buildId).LastWriteTimeUtc -ge $latestInput
}

function Build-Frontend {
  if (Test-PresentationBuildCurrent) {
    Write-Cfs "Stable live frontend build is current."
    return
  }

  Write-Cfs "Building the stable live frontend."
  $env:NEXT_PUBLIC_CFS_DEPLOYMENT_MODE = "live"
  $env:NEXT_PUBLIC_CFS_RUNTIME_MODE = "local"
  $env:NEXT_PUBLIC_CFS_DATA_PROVIDER = "local_api"
  $env:NEXT_PUBLIC_CFS_AUTH_MODE = "local_dev"
  $env:NEXT_PUBLIC_CFS_AI_PROVIDER = if ($EnableOpenAI) { "openai" } else { "none" }
  $env:NEXT_PUBLIC_CFS_ARTIFACT_PROVIDER = "local_file"
  $env:NEXT_PUBLIC_CFS_JOB_PROVIDER = "inline"
  $env:NEXT_PUBLIC_USE_BACKEND_API = "true"
  $env:NEXT_PUBLIC_CFS_API_BASE_URL = $ApiBaseUrl
  $env:NEXT_PUBLIC_CFS_ONLINE_BASEMAP = "false"
  Push-Location $Root
  try {
    & npm.cmd run build *> $BuildLog
    if ($LASTEXITCODE -ne 0) {
      $tail = (Get-Content -Tail 25 -LiteralPath $BuildLog) -join [Environment]::NewLine
      throw "Frontend build failed. Last log lines:`n$tail"
    }
  } finally {
    Pop-Location
  }

  $standalone = Join-Path $Root ".next\standalone"
  New-Item -ItemType Directory -Path (Join-Path $standalone ".next") -Force | Out-Null
  Copy-Item -LiteralPath (Join-Path $Root ".next\static") -Destination (Join-Path $standalone ".next\static") -Recurse -Force
  Copy-Item -LiteralPath (Join-Path $Root "public") -Destination (Join-Path $standalone "public") -Recurse -Force

  @{
    build_id = (Get-Content -Raw -LiteralPath (Join-Path $Root ".next\BUILD_ID")).Trim()
    built_at = (Get-Date).ToUniversalTime().ToString("o")
    commit = Get-GitHead
    deployment_mode = "live"
    backend_api = $true
    api_base_url = $ApiBaseUrl
    online_basemap = $false
  } | ConvertTo-Json | Set-Content -LiteralPath $BuildMarker -Encoding utf8
}

function Wait-Http {
  param(
    [string]$Url,
    [int]$TimeoutSeconds
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  do {
    try {
      return Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 10
    } catch {
      Start-Sleep -Seconds 1
    }
  } while ((Get-Date) -lt $deadline)
  throw "Timed out waiting for $Url."
}

function Start-CfsDetachedProcess {
  param(
    [string]$Command,
    [string]$WorkingDirectory
  )

  $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Command))
  $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
  $result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = "powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand $encoded"
    CurrentDirectory = $WorkingDirectory
    ProcessStartupInformation = $startup
  }
  if ($result.ReturnValue -ne 0 -or !$result.ProcessId) {
    throw "Windows could not start the detached CFS process (code $($result.ReturnValue))."
  }
  return [pscustomobject]@{ Id = [int]$result.ProcessId }
}

function Start-Backend {
  $enabled = if ($EnableOpenAI) { "true" } else { "false" }
  $provider = if ($EnableOpenAI) { "openai" } else { "none" }
  $command = @"
Set-Location -LiteralPath '$Backend'
`$env:DATABASE_URL=''
`$env:POSTGRES_HOST='$PostgresHost'
`$env:POSTGRES_PORT='$PostgresPort'
`$env:POSTGRES_DB='$PostgresDb'
`$env:CFS_DATABASE_AUTH_MODE='password'
`$env:CFS_RUNTIME_MODE='local'
`$env:CFS_DATA_PROVIDER='local_api'
`$env:CFS_AUTH_MODE='local_dev'
`$env:CFS_ARTIFACT_PROVIDER='local_file'
`$env:CFS_JOB_PROVIDER='inline'
`$env:CFS_PRESENTATION_CACHE_ENABLED='true'
`$env:CFS_PRESENTATION_FREEZE_ID='thursday-demo-2026-09-16'
`$env:CFS_DATABASE_POOL_SIZE='10'
`$env:CFS_DATABASE_MAX_OVERFLOW='10'
`$env:CFS_DATABASE_POOL_TIMEOUT_SECONDS='30'
`$env:CFS_AI_ENABLED='$enabled'
`$env:CFS_AI_PROVIDER='$provider'
& '$Python' -m uvicorn app.main:app --host 127.0.0.1 --port $BackendPort *> '$BackendLog'
"@
  Write-Cfs "Starting FastAPI in $(if ($EnableOpenAI) { 'optional OpenAI' } else { 'deterministic local' }) mode."
  return Start-CfsDetachedProcess -Command $command -WorkingDirectory $Backend
}

function Start-Frontend {
  $command = @"
Set-Location -LiteralPath '$Root'
`$env:HOSTNAME='127.0.0.1'
`$env:PORT='$FrontendPort'
`$env:CFS_PROJECT_ROOT='$Root'
& '$Node' '$FrontendServer' *> '$FrontendLog'
"@
  Write-Cfs "Starting Next.js production server."
  return Start-CfsDetachedProcess -Command $command -WorkingDirectory $Root
}

New-Item -ItemType Directory -Path $Logs -Force | Out-Null

try {
  Write-Cfs "Checking local presentation prerequisites."
  foreach ($command in @("node", "npm.cmd", "git")) {
    Assert-Command -Name $command
  }
  Assert-Command -Name $Python
  Invoke-Checked -FailureMessage "Required Python packages are unavailable." -Command {
    & $Python -c "import fastapi, uvicorn, sqlalchemy, psycopg, pydantic_settings, shapely"
  }
  if (!(Test-TcpPort -HostName $PostgresHost -Port $PostgresPort)) {
    throw "PostgreSQL is unavailable at localhost:$PostgresPort. Start the local cfs_dev service, then retry."
  }

  $env:DATABASE_URL = ""
  $env:POSTGRES_HOST = $PostgresHost
  $env:POSTGRES_PORT = "$PostgresPort"
  $env:POSTGRES_DB = $PostgresDb
  $env:CFS_DATABASE_AUTH_MODE = "password"
  $dataStarted = Get-Date
  Invoke-Checked -FailureMessage "Local cfs_dev/PostGIS readiness failed." -Command {
    & $Python $DataCheck
  }
  $dataReadyMs = [math]::Round(((Get-Date) - $dataStarted).TotalMilliseconds, 1)

  Ensure-FrontendEnv

  if ($NoRestart) {
    Write-Cfs "NoRestart supplied; validating the existing CFS listeners."
    if ($FrontendOnly) {
      & $StopScript -FrontendOnly -CheckOnly
    } elseif ($BackendOnly) {
      & $StopScript -BackendOnly -CheckOnly
    } else {
      & $StopScript -CheckOnly
    }
  } else {
    if ($FrontendOnly) {
      & $StopScript -FrontendOnly -CheckOnly
    } elseif ($BackendOnly) {
      & $StopScript -BackendOnly -CheckOnly
    } else {
      & $StopScript -CheckOnly
    }
    if ($FrontendOnly) {
      & $StopScript -FrontendOnly
    } elseif ($BackendOnly) {
      & $StopScript -BackendOnly
    } else {
      & $StopScript
    }

    if (!$BackendOnly) { Build-Frontend }

    if (!$FrontendOnly) {
      $backendStartedAt = Get-Date
      $backendProcess = Start-Backend
      $startedBackend = $true
    }
    if (!$BackendOnly -and $FrontendOnly) {
      $frontendStartedAt = Get-Date
      $frontendProcess = Start-Frontend
      $startedFrontend = $true
    }
  }

  $backendReady = Wait-Http -Url "$ApiBaseUrl/health/ready" -TimeoutSeconds 90
  $backendReadyMs = if ($backendStartedAt) {
    [math]::Round(((Get-Date) - $backendStartedAt).TotalMilliseconds, 1)
  } else {
    0
  }
  Write-Cfs "Backend ready: HTTP $($backendReady.StatusCode)."

  $cacheStartedAt = Get-Date
  Invoke-Checked -FailureMessage "Local presentation cache prewarm failed." -Command {
    & $Python $CachePrewarm
  }
  $cacheReadyMs = [math]::Round(((Get-Date) - $cacheStartedAt).TotalMilliseconds, 1)
  $cacheReady = Invoke-RestMethod -Uri "$ApiBaseUrl/health/presentation-cache" -TimeoutSec 15
  Write-Cfs "Presentation cache ready: $($cacheReady.entry_count) entries."

  if (!$BackendOnly -and !$FrontendOnly) {
    $frontendStartedAt = Get-Date
    $frontendProcess = Start-Frontend
    $startedFrontend = $true
  }

  if (!$BackendOnly) {
    $frontendReady = Wait-Http -Url $FrontendUrl -TimeoutSeconds 180
    $frontendReadyMs = if ($frontendStartedAt) {
      [math]::Round(((Get-Date) - $frontendStartedAt).TotalMilliseconds, 1)
    } else {
      0
    }
    Write-Cfs "Frontend ready: HTTP $($frontendReady.StatusCode)."
  } else {
    $frontendReadyMs = 0
  }

  $aiStatus = Invoke-RestMethod -Uri "$ApiBaseUrl/ai/status" -TimeoutSec 15
  $totalMs = [math]::Round(((Get-Date) - $startedAt).TotalMilliseconds, 1)
  $summary = @{
    status = "PASS"
    checked_at = (Get-Date).ToUniversalTime().ToString("o")
    frontend = $FrontendUrl
    backend = $ApiBaseUrl
    database = "cfs_dev"
    data_readiness_ms = $dataReadyMs
    backend_startup_ms = $backendReadyMs
    presentation_cache_ms = $cacheReadyMs
    presentation_cache_entries = $cacheReady.entry_count
    frontend_startup_ms = $frontendReadyMs
    total_startup_ms = $totalMs
    ask_cfs = if ($aiStatus.ai_enabled -and $aiStatus.configured_provider -eq "openai" -and $aiStatus.api_key_configured -and $aiStatus.model_configured) { "optional provider active" } else { "deterministic local" }
    frontend_process_id = if ($frontendProcess) { $frontendProcess.Id } else { $null }
    backend_process_id = if ($backendProcess) { $backendProcess.Id } else { $null }
  }
  $summary | ConvertTo-Json | Set-Content -LiteralPath $StartupReport -Encoding utf8

  Write-Host ""
  Write-Cfs "PASS - Local presentation is ready in $([math]::Round($totalMs / 1000, 1)) seconds."
  Write-Cfs "Home: $FrontendUrl"
  Write-Cfs "API:  $ApiBaseUrl"
  Write-Cfs "Ask CFS: $($summary.ask_cfs)"
  Write-Cfs "Stop safely with: npm run stop:cfs"
} catch {
  if (!$NoRestart) {
    if ($startedFrontend) {
      & $StopScript -FrontendOnly -Quiet
    }
    if ($startedBackend) {
      & $StopScript -BackendOnly -Quiet
    }
  }
  Write-Error $_.Exception.Message
  exit 1
}
