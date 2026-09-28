# CampusFix — Claude Project Guide

## Project Overview

CampusFix is a campus issue-reporting and maintenance platform. Students, faculty, and staff submit campus issues through a web interface. Tickets are classified and routed, managed by department staff, and surfaced through administrative analytics.

Product flow:

User submits issue → ticket created → AI classification → department/category/priority suggestion → high-confidence auto-routing or low-confidence review → department queue → assignment → work → resolution → requester notification → analytics.

Each ticket has exactly one primary department. Multi-department ownership is explicitly out of scope for the MVP.

## Platform

The project runs on **Supabase** (hosted PostgreSQL + Auth + Storage + Row Level Security).

- Database: Supabase Postgres — schema in `supabase/migrations/`
- Auth: Supabase Auth (`auth.users`); `public.users` extends it with role and profile data
- Storage: Supabase Storage for ticket attachments, accessed via signed URLs
- Authorization: **Row Level Security policies are the primary enforcement mechanism**
- Frontend: React + TypeScript
- Backend: FastAPI + Python, for AI classification, analytics aggregates, and notifications
- Maps: Leaflet or MapLibre with OpenStreetMap

The Supabase MCP server is configured in `.mcp.json`. Use it to inspect live schema, run read queries, and check RLS policies rather than assuming the SQL file matches production.

## Authorization Model

This is the most important architectural decision in the project, and it differs from a conventional FastAPI app.

**RLS policies in the database are the source of truth for who can read and write what.** A student cannot read another student's ticket even if every line of application code is wrong, because the database itself refuses. This directly satisfies the project's headline security requirement.

Consequences to respect:

- Client requests from React carry the user's JWT and run under that user's RLS context.
- FastAPI uses the `service_role` key, which **bypasses RLS entirely**. Any FastAPI endpoint that touches user data must re-check authorization in Python. Treat `service_role` as root.
- Never expose the `service_role` key to the frontend or commit it. Only the publishable key belongs in client code.
- Adding a new table means adding RLS policies in the same migration. A table with RLS enabled and no policies denies everything.

**Two independent gates control Data API access, and confusing them wastes hours:**

1. **Grants** decide whether `anon`/`authenticated` can touch the table *at all*. Supabase's default is to grant everything on `public`; this project has revoked that and grants explicitly per table. `alter default privileges` ensures new tables do not inherit broad access.
2. **RLS policies** decide *which rows* are visible once the table is reachable.

A table can therefore be invisible to the API despite having perfectly correct RLS policies, because no grant exists. The symptom is a permission error rather than an empty result. Empty result means RLS filtered the rows; permission denied means the grant is missing.

Also note: Postgres grants `EXECUTE` to `PUBLIC` on every new function, so any function in `public` is automatically a callable RPC endpoint at `/rest/v1/rpc/<name>`. Revoke it for anything not meant to be an API.

## Primary Roles

- **Student/User** — submit tickets, upload images, view only their own tickets, comment, receive updates, request reopen within the allowed window, delete their own ticket where permitted
- **Department Staff** — view tickets for their department, accept/assign, update status, add notes/comments, resolve
- **Department Admin** — everything staff can do, plus department analytics, staff management, and ticket transfer/reassignment
- **System Admin** — all departments, system-wide analytics, user management, routing rules, system configuration

Roles live in `public.roles` and are referenced by `public.users.role_id`. Department membership for staff and admins lives in `public.user_departments`.

## Schema as Built

The schema is implemented and applied. Do not propose adding these — they exist:

| Concern | Where it lives |
|---|---|
| Departments | `departments`, `tickets.department_id` |
| AI suggestions | `tickets.ai_suggested_department_id`, `ai_suggested_issue_type_id`, `ai_suggested_priority_id`, `ai_confidence`, `ai_routing_status` |
| Resolution notes | `tickets.resolution_notes` |
| Reopen workflow | `ticket_reopen_requests` |
| Phone number | `users.phone` |
| Staff department membership | `user_departments` |
| Private staff notes | `comments.is_internal` |
| Soft delete | `tickets.deleted_at` |
| Audit trail | `ticket_history` |

### Location hierarchy

`buildings → floors → locations → tickets`

`locations` does **not** store `building_id`; the building is derived through `floor_id`. Do not reintroduce `building_id` — it creates the possibility of a location whose building and floor disagree.

### Effective vs. suggested values

`department_id`, `issue_type_id`, and `priority_id` hold the **effective** value. The `ai_suggested_*` columns hold what the AI recommended. They are deliberately separate so an override is visible and auditable.

`ai_routing_status` is one of `PENDING`, `AUTO_ROUTED`, `NEEDS_REVIEW`, `ACCEPTED`, `OVERRIDDEN`, `FAILED`.

## Schema Gaps — Status

Closed by migration:

- **Auth integration** — `users.user_id` now references `auth.users(id)`. `handle_new_user()` provisions a profile on signup, always as `STUDENT`.
- **Notification delivery tracking** — `delivery_status`, `attempts`, `last_attempt_at`, `error_message`. V-032 is now testable.
- **`issue_types.default_department_id`** — deterministic routing fallback when AI is unavailable.
- **`updated_at` triggers** — `set_updated_at()` on `users`, `departments`, `tickets`, `comments`.
- **One pending reopen per ticket** — `ux_one_pending_reopen`.
- **`tickets.is_emergency`** — emergency handling is now auditable, not just a UI warning.
- **Seed data** — `issue_types` (33), `area_types` (15), `student_types` (5), `buildings` (9 real Caldwell buildings), `floors` (36).
- **`student_types`** — given a purpose: analytics segmentation of the requester population.

Still open:

1. **`locations.floor_id` stays nullable**, deliberately — outdoor locations such as a parking lot have no floor. `verification.md` V-030 was rewritten to assert referential validity when present rather than requiring presence.
2. **No routing-rules table.** Deferred to stretch. `issue_types.default_department_id` covers MVP routing.
3. **Buildings have no coordinates.** All nine real buildings are seeded, but `buildings.latitude`/`longitude` are NULL. The campus map cannot plot a building until they are filled in.
4. **`users.email` has a `CHECK (email LIKE '%@caldwell.edu')`.** This runs inside the signup trigger, so a non-Caldwell signup fails the whole `auth.users` insert with an opaque error. Surface a clear message in the UI, and note that test accounts must use a `@caldwell.edu` address.

## Verified Security Behavior

Tested directly against the database with simulated JWTs, not through the UI:

| Test | Result |
|---|---|
| Student reads another student's ticket by id (V-003) | denied, 0 rows |
| Student reads another student's profile | denied, 0 rows |
| Student sets own `role_id` to `SYSTEM_ADMIN` | blocked by column grant |
| Student edits own ticket directly | 0 rows; no UPDATE policy applies |
| Student submits ticket pre-set to `URGENT` / `RESOLVED` | clamped to `NEW`, null priority, null department |
| Anonymous (`anon`) reads tickets | permission denied |
| `get_advisors(security)` | 0 findings |

Re-run these after any migration that touches policies or grants.

## Engineering Rules

- Never let AI failure block ticket creation. AI failure means `ai_routing_status = 'FAILED'` and a review state, not a rejected submission.
- Low-confidence AI results go to review rather than silently guessing.
- Authorized staff can override AI department/category/priority, and the override is recorded in `ticket_history`.
- Students must never access another student's ticket by changing a URL or ticket ID. Enforce this with RLS, not just UI.
- Emergency language must produce a warning directing the user to emergency services or campus safety rather than relying on the normal queue.
- Existing tickets must survive department deactivation. Use `departments.active = false`, never a hard delete.
- Open tickets assigned to a departing staff member must remain reassignable.
- Notification failure must not roll back the underlying ticket change.
- Validate image type and file size before upload; serve attachments through signed URLs only.
- Avoid collecting unnecessary sensitive information.
- Write to `ticket_history` for every important state and assignment change.
- Enforce status transition rules in the service layer, not in database CHECK constraints.
- Use soft delete (`deleted_at`) for tickets. Administrators need the history for analytics and audit.

## Scope Discipline

2 developers, 1 designer, 1 PM, 13 weeks, roughly 6 hours per person per week. That is approximately **156 developer-hours total** for the entire project. Treat this as the binding constraint on every scope decision.

Build the core ticket lifecycle first. Analytics and AI sit on top of reliable ticket and history data; they are not prerequisites for basic issue submission.

Stretch features, in rough priority order: duplicate-ticket detection and flagging, advanced AI classification, asset tracking, QR-code asset reporting, predictive maintenance.

## Conventions

- Schema changes are migrations in `supabase/migrations/`, never ad-hoc edits in the Supabase dashboard. The dashboard is for inspection.
- Every migration that creates a table also enables RLS and adds its policies.
- Status, priority, and issue type names come from the seeded lookup tables. Do not hard-code them as string literals in application code.
- Secrets live in `.env`, which is gitignored. `.env.example` documents the required variables and is committed.
