# CampusFix — Milestones

## Budget Reality

- 13 weeks
- 2 developers, 1 designer, 1 product manager
- ~6 hours per person per week

**That is approximately 156 developer-hours for the entire project.** Every scope decision below is driven by that number. The priority is a complete, working, demonstrable product over a long list of half-finished features.

Two changes from the original plan, both made to fit the budget:

1. The campus map is folded into the analytics milestone. It is one dashboard, not two.
2. The old "Intelligence" and "AI / Stretch" milestones are merged. They described substantially the same work.

This recovers roughly two weeks, which is spent on testing and on the reopen and notification flows — the places where correctness is actually visible.

## Milestone 1 — Foundation
### Weeks 1–2

Deliver:

- Project repository, with frontend and backend workspaces
- React + TypeScript app with routing and a component skeleton
- FastAPI service with health check and Supabase client
- Supabase project with migrations under version control
- Supabase Auth wired to the React app: sign-in, sign-out, session persistence
- `public.users` trigger that provisions a profile row on `auth.users` insert
- **RLS policies for every existing table**
- Seed data completed: `issue_types`, `buildings`, `floors`, `area_types` (roles, statuses, priorities, departments are already seeded)
- Schema fixes 7.1 through 7.7 from `architecture.md`, applied as migrations
- Initial Figma flows (designer, parallel)

Exit criteria:

- A user can sign in and sign out.
- A student querying another student's ticket id gets nothing back — verified against the live database, not just the UI.
- Both apps run locally from a documented setup.

Do not move past this milestone with RLS unfinished. Every later milestone assumes it.

## Milestone 2 — Core Ticketing
### Weeks 3–4

Deliver:

- Student ticket submission form, mobile-usable
- Minimum-information validation
- **Emergency keyword detection and warning interstitial**
- Ticket creation path
- Ticket detail page
- Student ticket list
- Comments, with `is_internal` respected
- Image attachments: private bucket, signed URLs, type and size validation
- `ticket_history` writing on create and on every change
- Staff queue, filterable by status, priority, building, and date

Exit criteria:

A student can submit a ticket from a phone, and a staff member can see and open it. Emergency language produces the warning. Authorization tests for cross-user access pass.

## Milestone 3 — Assignment and Workflow
### Weeks 5–6

Deliver:

- Department routing, using `issue_types.department_id` as the deterministic fallback
- Staff assignment and reassignment, preserving assignment history
- Status transitions with service-layer rules
- Internal staff notes
- Resolution with notes and `resolved_at`
- Notifications, with delivery status tracking and retry
- Reopen request flow with the seven-day window
- Admin accept/deny for reopen requests
- Ticket transfer between departments

Exit criteria:

A ticket moves from submission through assignment, resolution, reopen request, and admin decision, with a complete and correct audit trail at every step. Notification failure does not roll back the ticket change.

This is the heart of the product. If the schedule slips, protect this milestone and cut from Milestones 5 and 6.

## Milestone 4 — Hardening
### Week 7

Deliver:

- Authorization test suite covering the full matrix in `verification.md` §8
- Edge-case tests: staff departure, department deactivation, post-resolution reply, oversized and wrong-type uploads
- Error, empty, and loading states across existing screens
- Error logging
- Seeded demo dataset: several hundred tickets across buildings, departments, and issue types, with realistic history

Exit criteria:

The core product is trustworthy and there is enough data to make the next two milestones meaningful.

This milestone exists because testing at week 12 only, after the riskiest work, is how capstones fail their demo.

## Milestone 5 — Analytics and Map
### Weeks 8–10

Deliver:

- Ticket volume, open vs. resolved
- Tickets by department, issue type, and building
- Average resolution time
- Aging tickets
- Trends over time
- Admin map with ticket markers
- Shared filter set across charts and map: building, status, priority, category, date

Exit criteria:

An admin dashboard displays metrics computed from real ticket and history data, and authorized users can inspect ticket distribution across campus. No student can reach any of it.

## Milestone 6 — AI Classification
### Weeks 10–11

Deliver:

- Classification service: department, issue type, priority, confidence
- Configurable confidence threshold
- Auto-route path and `NEEDS_REVIEW` path
- Manual override, recorded in history
- Graceful failure to `NEEDS_REVIEW`
- Recurring issue and hotspot detection, if time allows

Exit criteria:

The system demonstrates meaningful classification and routing on the seeded dataset, and behaves correctly with the AI service switched off.

Build and test the AI-disabled path **first**. The demo must survive an API outage or an expired key on presentation day.

## Milestone 7 — Stretch
### Week 12, only if Milestones 1–6 are stable

Candidates, in priority order:

- Duplicate detection and flagging
- Improved classification prompts and confidence calibration
- Asset tracking prototype
- QR-code asset reporting

Do not let stretch work destabilize the core product. If anything here breaks something above it, revert.

## Milestone 8 — Deployment and Polish
### Week 13

Deliver:

- Deployment of frontend and backend
- Production Supabase configuration and environment separation
- Responsive and accessibility pass
- UI polish
- Architecture and product documentation, brought current
- Demo script with a rehearsed happy path
- **Offline backup plan**: recorded demo video and a local fallback, in case of network or service failure during presentation
- Final verification pass against `verification.md`

## Schedule Summary

| Weeks | Milestone |
|---|---|
| 1–2 | Foundation |
| 3–4 | Core Ticketing |
| 5–6 | Assignment and Workflow |
| 7 | Hardening |
| 8–10 | Analytics and Map |
| 10–11 | AI Classification |
| 12 | Stretch |
| 13 | Deployment and Polish |

Weeks 10 overlaps deliberately: analytics polish runs alongside the start of AI work, since they touch different parts of the codebase and different developers can own them.

## Definition of Done

A feature is done when:

1. The frontend is usable, including empty, loading, and error states.
2. The backend path works.
3. Database state is correct, including `ticket_history` entries.
4. Authorization is enforced **at the database**, not only in the UI.
5. Error and failure cases are handled.
6. Tests exist for the authorization rules and the important logic.
7. The feature is deployed or integrated on the shared branch.
8. Documentation is updated where the change affects it.
