# CampusFix

Campus issue reporting and maintenance platform. Students report problems,
departments triage and resolve them, administrators see the patterns.

- `product-spec.md` — what it does and why
- `architecture.md` — how it is built, including the RLS model
- `milestones.md` — the 13-week plan
- `verification.md` — the test plan
- `CLAUDE.md` — working notes and conventions

## Stack

| Layer | Choice |
|---|---|
| Database, auth, storage | Supabase (PostgreSQL + Auth + Storage + RLS) |
| Frontend | React 18 + TypeScript + Vite |
| Backend | FastAPI (AI routing, writing enrichment, notification email) |
| Local AI | DeepSeek-R1 8B through Ollama |
| Authorization | **Row Level Security policies in the database** |

The important architectural point: authorization is enforced by the database,
not by the application. A student cannot read another student's ticket even
if the frontend is wrong, because Postgres refuses the row.

## Setup

Requires Node 20+, Python 3.11+, and local Ollama for AI features.

### 1. Frontend

```bash
cd frontend
cp .env.example .env     # values are already filled in; none are secret
npm install
npm run dev              # open the Vite URL printed (usually 5173 or 5174)
```

That is enough to run the whole product. The backend is optional.

### 2. Backend (optional — AI classification)

```bash
cd backend
cp .env.example .env
# Edit .env and add SUPABASE_SERVICE_ROLE_KEY for classification.
# The API still starts if the key is blank.
python -m venv .venv
./.venv/Scripts/python.exe -m pip install --only-binary=:all: -r requirements.txt -c requirements.lock.txt
ollama pull deepseek-r1:8b
# Keep Ollama running on localhost:11434 (ollama serve if needed).
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

Check it with `curl http://localhost:8000/api/health`.

Without the backend, tickets route deterministically from their issue type.
The backend uses local **DeepSeek-R1 8B** to predict **issue type and priority**.
Department is derived from `issue_types.default_department_id`, with the existing
CampusFix exceptions: Vending → IT and dorm lockout/lost room key → Residence
Life. Taxonomy is cached in the backend process; restart it after taxonomy changes.

Successful decisions with a valid department become `AUTO_ROUTED`. Vague,
ambiguous, multiple-issue, and `Other` decisions become `NEEDS_REVIEW`.
The existing `confidence` field carries a routing compatibility flag: `1.0`
for successful decisions and `0.0` for review. It is not a calibrated probability.
Model/connection failures mark classification `FAILED` and leave the ticket
available for manual review. Emergency tickets retain their existing effective
department, priority, and status on success, review, or failure.

After successful routing is saved, a separate enrichment call preserves
the verbatim `original_title` and `original_text` snapshots and generates a
cleaned `title`, `description`, and `ai_explanation`. Enrichment writes only
those text fields. Its failure cannot undo routing or turn a successful classification into an API error. Ticket
creation never depends on either model call succeeding. The backend polls
Supabase for non-deleted tickets still marked `PENDING` every seven seconds,
starting five seconds after creation, and routes them asynchronously. The
worker runs only while the AI-enabled backend is running; classification
failures are recorded as `FAILED` for staff review.

Classification and enrichment share `OLLAMA_URL`, `OLLAMA_MODEL` (default
`deepseek-r1:8b`), and `OLLAMA_TIMEOUT_SECONDS` (default `180`) from `backend/.env`.
Set `AI_CLASSIFICATION_ENABLED=false` to disable classification. `/api/health`
reports whether AI is configured; it does not probe whether Ollama is running.

Run backend regression tests from `backend` with
`python -m unittest discover -s tests -v`. These exercise the API and the real
Supabase client against an in-memory HTTP transport, without remote writes.
Run frontend request regression tests from `frontend` with `npm test`, followed
by `npm run typecheck` and `npm run build`.

### Local AI worker for the Netlify frontend

The browser writes tickets directly to Supabase. The local FastAPI backend
polls Supabase for pending tickets, classifies them with Ollama, and writes the
result back. No public backend URL, tunnel, browser-to-backend request, CORS
allowlist, or `VITE_API_URL` is needed. Supabase remains hosted as usual.

The backend waits five seconds after ticket creation, then checks the queue
every seven seconds. It only starts this worker when AI and the private
service-role key are configured. The backend machine must stay on, connected,
and running Ollama and FastAPI; configure FastAPI to start at Windows sign-in
with [the local AI worker guide](docs/LOCAL_AI_WORKER.md).

Use [the local demo guide](docs/LOCAL_DEMO.md) for sample tickets and
troubleshooting. The ticket page refreshes its row while routing is pending and
once after routing completes to pick up separate text enrichment. The stored AI
routing explanation is displayed; staff can expand original student text when
it differs from the polished version. Drafts and action forms remain intact.
Model, policy, and frozen benchmarks are unchanged.

### Final AI holdout results

The frozen **62-ticket holdout** was kept separate from development and tuning.
These are the recorded final results; cleanup does not rerun the holdout or
modify its saved predictions.

| Model | Issue type accuracy | Department accuracy | Priority accuracy |
|---|---:|---:|---:|
| **DeepSeek-R1 8B (final implementation)** | **82.3%** | **82.3%** | **64.5%** |
| Qwen3 8B (comparison only) | 80.6% | 85.5% | 54.8% |

These recorded measurements cover routing; they do not evaluate writing enrichment.
The [evaluation archive](evaluation/README.md) includes the exact 249-case dataset
(187 development / 62 holdout), historical comparison evaluator, both frozen
prediction files, hashes, and accuracy/F1/latency results. Reproduce the metrics
without model calls or dependencies from the repository root:

```bash
python evaluation/reproduce_metrics.py --check
```

The holdout was used once for final model selection and must not be used for
future tuning or inference comparisons. See the [frozen pre-RAG baseline](docs/AI_BASELINE.md)
for architecture, limits, configuration, original-submission preservation, and
the evaluation freeze policy. No holdout inference was rerun for this freeze.

### 3. Database

Migrations in `supabase/migrations/` are the source of truth. Before enabling
writing enrichment on an existing deployment, apply the committed
`20261023023720_ticket_ai_explanation.sql` migration through your normal migration workflow.
It adds nullable `tickets.ai_explanation` text without changing existing reports
or routing permissions. Also apply `20261025203513_ticket_original_title.sql`
before deploying this frontend: it adds nullable `tickets.original_title`,
captures titles on insert, and protects the original on later updates. Existing
rows remain null rather than copying an already-polished title. This work does
not apply migrations to the hosted database. Never edit schema through the dashboard.

## Campus map

The map page renders a static campus image with a pin per building. Save the
campus map as:

```
frontend/public/campus-map.png
```

Pin positions live in `buildings.map_x` / `map_y` as **percentages of the image**,
not pixels, so they stay correct at any screen size and survive the image being
re-exported larger or smaller. They are seeded from the official campus map
legend, but they are estimates read off the artwork.

To correct one: sign in as a SYSTEM_ADMIN, open **Map**, press **Move** beside a
building, then click where the pin belongs.

If the image is cropped differently from the version the pins were seeded
against (for instance with the legend removed), every pin will be offset the
same way and each needs repositioning once.

## Granting staff access

Every new account is created as a `STUDENT`. Roles are never self-assigned —
that is deliberate, and it is enforced by a column-level grant, so a student
cannot promote themselves even by calling the API directly.

To promote someone, run this in the Supabase SQL editor:

```sql
-- Make a user a department staff member for Facilities.
update public.users
   set role_id = (select role_id from public.roles where name = 'DEPARTMENT_STAFF')
 where email = 'someone@caldwell.edu';

insert into public.user_departments (user_id, department_id)
select u.user_id, d.department_id
  from public.users u, public.departments d
 where u.email = 'someone@caldwell.edu'
   and d.name = 'Facilities'
on conflict do nothing;
```

Roles: `STUDENT`, `DEPARTMENT_STAFF`, `DEPARTMENT_ADMIN`, `SYSTEM_ADMIN`.
A `SYSTEM_ADMIN` sees every department and needs no `user_departments` row.

## Accounts

Sign-up requires a `@caldwell.edu` address. This is a database CHECK
constraint, enforced inside the signup trigger, so a non-Caldwell address
fails the whole signup.

## Verifying security

The authorization model is testable without the UI. In the Supabase SQL
editor, impersonate a user and confirm the database refuses what it should:

```sql
begin;
set local role authenticated;
set local request.jwt.claims = '{"sub":"<some-user-uuid>","role":"authenticated"}';
select ticket_id from public.tickets;   -- only their own, or their department's
rollback;
```

Run `get_advisors(security)` from the Supabase MCP server, or the dashboard's
advisors page, after any migration. It should report zero findings.
