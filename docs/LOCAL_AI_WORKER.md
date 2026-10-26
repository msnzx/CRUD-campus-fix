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

## Start and stop for a demo

From the repository root in PowerShell, start both Ollama and the backend worker:

```powershell
.\scripts\start-ai-worker.ps1
```

When the demo is over, stop both:

```powershell
.\scripts\stop-ai-worker.ps1
```

In Command Prompt (`cmd.exe`), run the same scripts through PowerShell:

```bat
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-ai-worker.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stop-ai-worker.ps1
```

The start command launches Ollama if needed, verifies the configured model is
installed, then starts the worker in the background. The stop command stops the
worker and Ollama. The worker logs are saved under
`%LOCALAPPDATA%\CampusFix\`. Check the backend with
`Invoke-RestMethod http://127.0.0.1:8000/api/health`. Its `ai_enabled` field
reports configuration; it does not perform a model inference check.

The authenticated `/api/tickets/{ticket_id}/classify` endpoint remains
available for authorized manual requests, but the browser does not call it.
Classification errors are stored as `FAILED` for staff review. Tickets remain
visible and usable if this machine is offline; automatic routing resumes for
pending tickets when the worker restarts.

The worker and Ollama are not configured to start at sign-in. The start script
disables the legacy scheduled task and Ollama startup shortcut if they exist.
To inspect service health while running:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-RestMethod http://127.0.0.1:11434/api/tags
```

To remove the old scheduled task completely, if desired:

```powershell
Unregister-ScheduledTask -TaskName 'CampusFix AI Worker' -Confirm:$false
```
