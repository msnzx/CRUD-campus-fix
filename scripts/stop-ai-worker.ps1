$ErrorActionPreference = 'Stop'

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

$backendApi = 'http://127.0.0.1:8000'
$listener = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($listener) {
    $worker = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
    if (-not $worker) {
        $listener = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue |
            Select-Object -First 1
    }
    if (-not $worker -and $listener) {
        throw "Port 8000 belongs to another process (PID $($listener.OwningProcess)); refusing to stop it."
    }
    if ($worker -and $worker.CommandLine -notmatch 'uvicorn\s+app\.main:app') {
        throw "Port 8000 belongs to another process (PID $($listener.OwningProcess)); refusing to stop it."
    }
    if ($worker) {
        Stop-Process -Id $worker.ProcessId -Force
        $deadline = (Get-Date).AddSeconds(15)
        do {
            Start-Sleep -Milliseconds 500
            $listener = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue
        } while ($listener -and (Get-Date) -lt $deadline)
        if ($listener) {
            throw 'The backend worker did not release port 8000.'
        }
    }
}

$ollamaRoot = Join-Path $env:LOCALAPPDATA 'Programs\Ollama'
$ollamaProcesses = Get-CimInstance Win32_Process |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith("$ollamaRoot\", [StringComparison]::OrdinalIgnoreCase) } |
    Sort-Object @{
        Expression = {
            if ($_.Name -eq 'llama-server.exe') { 0 }
            elseif ($_.Name -eq 'ollama.exe') { 1 }
            else { 2 }
        }
    }
foreach ($process in $ollamaProcesses) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}

Write-Host 'CampusFix AI worker and Ollama have been stopped.'
Write-Host 'They will not start automatically at sign-in.'
