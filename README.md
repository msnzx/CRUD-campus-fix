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
| Backend | FastAPI (AI classification only) |
| Authorization | **Row Level Security policies in the database** |

The important architectural point: authorization is enforced by the database,
not by the application. A student cannot read another student's ticket even
if the frontend is wrong, because Postgres refuses the row.

## Setup

Requires Node 20+ and Python 3.12+.

### 1. Frontend

```bash
cd frontend
cp .env.example .env     # values are already filled in; none are secret
npm install
npm run dev              # http://localhost:5173
```

That is enough to run the whole product. The backend is optional.

### 2. Backend (optional — AI classification)

```bash
cd backend
cp .env.example .env
# Edit .env and add SUPABASE_SERVICE_ROLE_KEY, and ANTHROPIC_API_KEY if you
# want classification. Both may be left blank; the API still starts.
python -m venv .venv
./.venv/Scripts/python.exe -m pip install --only-binary=:all: -r requirements.txt
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

Check it with `curl http://localhost:8000/api/health`.

Without the backend, tickets route deterministically from their issue type.
Without `ANTHROPIC_API_KEY`, the backend routes everything to manual review.
Neither failure can prevent a ticket being created — that is a project rule,
not an accident.

### 3. Database

The schema is already applied to the hosted project. Migrations live in
`supabase/migrations/` and are the source of truth. Never edit schema through
the dashboard; write a migration.

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
