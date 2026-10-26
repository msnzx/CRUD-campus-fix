$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repoRoot 'backend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
$ollamaRoot = Join-Path $env:LOCALAPPDATA 'Programs\Ollama'
$ollamaApp = Join-Path $ollamaRoot 'ollama app.exe'
$ollamaCli = Join-Path $ollamaRoot 'ollama.exe'
$ollamaApi = 'http://127.0.0.1:11434'
$backendApi = 'http://127.0.0.1:8000'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Backend virtual environment not found at $python. Set up backend dependencies first."
}

$task = Get-ScheduledTask -TaskName 'CampusFix AI Worker' -ErrorAction SilentlyContinue
if ($task -and $task.State -eq 'Running') {
    Stop-ScheduledTask -TaskName 'CampusFix AI Worker'
}
if ($task -and $task.State -ne 'Disabled') {
    Disable-ScheduledTask -TaskName 'CampusFix AI Worker' | Out-Null
}

$startupShortcut = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\Ollama.lnk'
$disabledShortcut = "$startupShortcut.disabled"
if (Test-Path -LiteralPath $startupShortcut) {
    Move-Item -LiteralPath $startupShortcut -Destination $disabledShortcut
}

$model = 'deepseek-r1:8b'
$envFile = Join-Path $backend '.env'
if (Test-Path -LiteralPath $envFile) {
    $modelSetting = Get-Content -LiteralPath $envFile |
        Where-Object { $_ -match '^\s*OLLAMA_MODEL\s*=' } |
        Select-Object -First 1
    if ($modelSetting -match '^\s*OLLAMA_MODEL\s*=\s*(.*?)\s*$') {
        $model = $Matches[1].Trim().Trim('"').Trim("'")
    }
}

$ollamaReady = $false
try {
    $null = Invoke-RestMethod -Uri "$ollamaApi/api/tags" -TimeoutSec 3
    $ollamaReady = $true
} catch {
    if (-not (Test-Path -LiteralPath $ollamaApp)) {
        throw "Ollama is not responding and its app was not found at $ollamaApp."
    }
    Start-Process -FilePath $ollamaApp
}

if (-not $ollamaReady) {
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        try {
            $null = Invoke-RestMethod -Uri "$ollamaApi/api/tags" -TimeoutSec 3
            $ollamaReady = $true
            break
        } catch {
            Start-Sleep -Seconds 2
        }
    }
}
if (-not $ollamaReady) {
    throw 'Ollama did not start within 60 seconds.'
}

$tags = Invoke-RestMethod -Uri "$ollamaApi/api/tags" -TimeoutSec 10
if (-not ($tags.models | Where-Object { $_.name -eq $model -or $_.name -eq "$model:latest" })) {
    throw "Ollama model '$model' is not installed. Install it with: & '$ollamaCli' pull $model"
}

$backendAlreadyRunning = $false
try {
    $health = Invoke-RestMethod -Uri "$backendApi/api/health" -TimeoutSec 3
    if ($health.status -ne 'ok' -or -not $health.ai_enabled -or -not $health.admin_enabled) {
        throw 'A service is responding on port 8000, but it is not a ready CampusFix AI worker.'
    }
    $backendAlreadyRunning = $true
} catch {
    if ($_.Exception.Message -like 'A service is responding*') {
        throw
    }
}

if (-not $backendAlreadyRunning) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue
    if ($listener) {
        throw 'Port 8000 is occupied by another process. Stop it before starting the CampusFix worker.'
    }

    $logDirectory = Join-Path $env:LOCALAPPDATA 'CampusFix'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $stdoutLog = Join-Path $logDirectory 'worker.stdout.log'
    $stderrLog = Join-Path $logDirectory 'worker.stderr.log'
    Start-Process -FilePath $python `
        -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000') `
        -WorkingDirectory $backend `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog | Out-Null

    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri "$backendApi/api/health" -TimeoutSec 3
            if ($health.status -eq 'ok' -and $health.ai_enabled -and $health.admin_enabled) {
                $backendAlreadyRunning = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 2
        }
        Start-Sleep -Seconds 2
    }
}

if (-not $backendAlreadyRunning) {
    throw 'The backend did not become ready within 60 seconds. Check %LOCALAPPDATA%\CampusFix\worker.stderr.log.'
}

Write-Host "CampusFix AI worker is running at $backendApi."
Write-Host "Ollama model '$model' is available at $ollamaApi."
Write-Host 'Stop both with .\scripts\stop-ai-worker.ps1.'
