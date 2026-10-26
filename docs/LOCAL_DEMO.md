# Local presentation workflow

Use the current `akoK3/CRUD-campus-fix` checkout, not an older experimental copy.
On this machine the final checkout is `C:\Users\kakij\Downloads\CRUD-campus-fix-final`.
The pre-RAG model/policy and frozen evaluation remain at the documented baseline.

## One-time checks before presenting

- Sign in with an existing account. Confirm you can create/read your own tickets.
  Use an authorized staff/admin account to show staff-only controls.
- Apply the committed `ticket_ai_explanation` and `ticket_original_title`
  migrations through the normal deployment workflow if not already applied.
  This demo fix changes no database schema or permissions.
- Backend: Python 3.11+ virtual environment and pinned requirements installed.
  If needed, from `backend`: `py -3.11 -m venv .venv`, then
  `.\.venv\Scripts\python.exe -m pip install -r requirements.txt -c requirements.lock.txt`.
- Keep the private Supabase server configuration in `backend/.env`. Do not copy
  it into the frontend or Git. The backend needs its service-role key locally.
- Ollama must have `deepseek-r1:8b` installed (`ollama pull deepseek-r1:8b`
  once if necessary) and serve only on loopback port 11434.

`backend/.env` must include:

```dotenv
OLLAMA_URL=http://localhost:11434/api/chat
OLLAMA_MODEL=deepseek-r1:8b
OLLAMA_TIMEOUT_SECONDS=180
AI_CLASSIFICATION_ENABLED=true
```

The backend requires no public URL, tunnel, CORS allowlist, or frontend API
environment variable. The browser writes each ticket to Supabase; the local
worker polls Supabase and routes tickets itself. See
[the local AI worker guide](LOCAL_AI_WORKER.md) to start the backend at Windows
sign-in.

## Start the demo

Ollama's Windows app may already be serving. If not, start it:

```powershell
ollama serve
```

Start the backend from the repository root:

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

For local frontend development, start Vite in another terminal:

```powershell
Set-Location frontend
npm run dev
```

Open the Vite URL it prints. The deployed Netlify frontend works the same way;
it needs no connection to this machine.

## Verify and demonstrate

Check backend configuration:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

Submit a new demo ticket (not a frozen holdout case):

- Title: `Bathroom sink clogged`
- Description: `The sink in my dorm bathroom is clogged and the water is draining very slowly.`
- Category: `Not sure - let CampusFix decide`
- Location: choose a real building/location in the form; location validation
  still applies even when category is blank.

The approximate expected result is **Plumbing / Facilities / MEDIUM /
AUTO_ROUTED**. This is a model prediction, not a hard-coded demo rule. Tickets
are routed by the local worker within seconds of being saved, subject to model
inference time. The ticket page refreshes its row while routing is `PENDING` and
once after routing completes to pick up separate text enrichment.

The worker only runs while the AI-enabled backend is running. Keep this machine
awake and connected to Supabase; Ollama and FastAPI must both be available.
Classification failures are recorded as `FAILED` for staff review.
