# Local AI worker for the Netlify frontend

The browser writes tickets directly to hosted Supabase. One local FastAPI
process polls Supabase for tickets whose `ai_routing_status` is `PENDING`,
calls Ollama over loopback, and saves the classification. The worker checks
every seven seconds and considers tickets after five seconds, so normal routing
starts within seconds plus model inference time.

There is no inbound network path: no Cloudflare tunnel, public backend URL,
CORS policy, or `VITE_API_URL`. Keep the Supabase service-role key only in
`backend/.env`; it bypasses RLS and must never be copied into the frontend.
The backend must be running for automatic routing, so keep this machine awake
and connected to the internet. Run exactly one backend worker to avoid duplicate
model work.

## Run manually

Ollama should be running on `127.0.0.1:11434`, with `deepseek-r1:8b` available.
From the repository root:

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Confirm configuration with `Invoke-RestMethod http://127.0.0.1:8000/api/health`.
The health endpoint's `ai_enabled` field reports configuration; it does not
perform a model inference check.

The authenticated `/api/tickets/{ticket_id}/classify` endpoint remains
available for authorized manual requests, but the browser does not call it.
Classification errors are stored as `FAILED` for staff review. Tickets remain
visible and usable if this machine is offline; automatic routing resumes for
pending tickets when the worker restarts.

## Start the worker at Windows sign-in

Run the following once in PowerShell as the Windows user who runs Ollama.
It registers one Task Scheduler task, waits briefly for the Ollama app to
start, starts FastAPI at sign-in, and ignores duplicate task launches:

```powershell
$backend = (Resolve-Path .\backend).Path
$python = Join-Path $backend '.venv\Scripts\python.exe'
$taskName = 'CampusFix AI Worker'
$action = New-ScheduledTaskAction `
  -Execute $python `
  -Argument '-m uvicorn app.main:app --host 127.0.0.1 --port 8000' `
  -WorkingDirectory $backend
$trigger = New-ScheduledTaskTrigger `
  -AtLogOn `
  -User "$env:USERDOMAIN\$env:USERNAME" `
  -RandomDelay (New-TimeSpan -Seconds 30)
$settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -MultipleInstances IgnoreNew `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal `
  -UserId "$env:USERDOMAIN\$env:USERNAME" `
  -LogonType Interactive `
  -RunLevel Limited
Register-ScheduledTask `
  -TaskName $taskName `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Principal $principal `
  -Description 'Poll Supabase for CampusFix tickets and route them with local Ollama.' `
  -Force
```

The task runs as the current user, reads the existing private `backend/.env`,
and listens only on loopback. To start or inspect it immediately:

```powershell
Start-ScheduledTask -TaskName 'CampusFix AI Worker'
Get-ScheduledTask -TaskName 'CampusFix AI Worker' | Select-Object TaskName, State
```

Do not also start Uvicorn manually while the task is running. To remove the
sign-in task later:

```powershell
Unregister-ScheduledTask -TaskName 'CampusFix AI Worker' -Confirm:$false
```
