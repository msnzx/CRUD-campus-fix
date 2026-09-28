# CampusFix — Product Specification

## 1. Product Summary

CampusFix is a campus-wide issue reporting and management platform. It lets students, faculty, and staff report problems, and gives departments a structured way to classify, route, assign, resolve, and analyze those requests.

The flow runs from user submission through AI classification, department routing, resolution, notification, and analytics.

## 2. Problem

Campus issues arrive through scattered channels — email, hallway conversations, phone calls — and often lack the information needed to route them efficiently. Nothing accumulates, so recurring problems stay invisible. CampusFix provides one structured intake path while preserving the data needed to analyze patterns across campus.

## 3. Goals

### Primary goals

- Make issue reporting simple enough that people actually use it.
- Capture enough information to route an issue without a follow-up conversation.
- Give departments a usable ticket queue.
- Track assignment and status changes with a complete audit trail.
- Notify requesters of important changes.
- Provide administrative analytics derived from real ticket data.
- Use AI as an assistive classification layer, never as a dependency for ticket creation.

### Non-goals for the MVP

- Multi-department ticket ownership
- Predictive maintenance
- Automatic duplicate merging
- Asset management
- AI beyond classification and routing

## 4. User Roles

Four role levels: Student/User, Department Staff, Department Admin, and System Admin.

### Student/User
Submit ticket, upload images, view own tickets, add comments, receive updates, request reopen during the allowed window, delete own ticket where permitted.

### Department Staff
View department tickets, accept and assign, update status, add notes, complete work, resolve.

### Department Admin
All staff capabilities, plus department analytics, staff management, and ticket transfer.

### System Admin
All-system visibility, cross-department analytics, user management, routing rules, system configuration.

## 5. Ticket Data

| Field | Column |
|---|---|
| Ticket ID | `tickets.ticket_id` |
| Requester | `tickets.reported_by` |
| Original submission text | `tickets.original_text` |
| Title | `tickets.title` |
| Description | `tickets.description` |
| Location, building, room | `tickets.location_id` → `locations` → `floors` → `buildings` |
| Category | `tickets.issue_type_id` |
| Department | `tickets.department_id` |
| Priority | `tickets.priority_id` |
| Status | `tickets.status_id` |
| Created / updated | `tickets.created_at`, `tickets.updated_at` |
| Assigned staff | `ticket_assignments` where `unassigned_at is null` |
| AI suggestions | `tickets.ai_suggested_*`, `ai_confidence`, `ai_routing_status` |
| Attachments | `attachments` |
| Resolution notes | `tickets.resolution_notes` |
| Resolved date | `tickets.resolved_at` |
| Requester email / phone | `users.email`, `users.phone` |

Contact details live on the user, not duplicated onto every ticket. `original_text` preserves the user's unedited words for auditing and future model training, separately from the title and description that staff may revise.

## 6. Ticket Lifecycle

```text
NEW → AI_PROCESSING ┬─ high confidence ──────────────► ASSIGNED
                    └─ low confidence / AI failure ──► NEEDS_REVIEW
                                                            │
                                                     staff picks dept
                                                            │
                                                            ▼
                                                        ASSIGNED
                                                            │
                                                            ▼
                                                       IN_PROGRESS ⇄ WAITING_FOR_USER
                                                            │
                                                            ▼
                                                        RESOLVED
                                                            │
                          ┌─────────────────────────────────┴──────────────┐
                          │                                                │
                 reopen request approved                          window expires
                          │                                        or user closes
                          ▼                                                │
                      REOPENED                                             ▼
                          │                                             CLOSED
                          └──────► IN_PROGRESS
```

`REOPENED` is entered only from `RESOLVED`, only within the reopen window, and only via an approved reopen request. It leads back into active work, not to `CLOSED`.

## 7. Ticket Submission

Required: title, description, location, requester. Optional: category, image.

The system rejects submissions with insufficient information. A description like "It's broken" cannot be routed, so the form enforces a minimum description length and prompts for a location before allowing submission.

The submission form must be usable on a phone, since a person reporting a broken fixture is standing in front of it.

## 8. AI Classification

AI may suggest department, category, priority, and a confidence score.

Rules:

- AI never blocks ticket creation. The ticket is committed before classification is attempted.
- Confidence at or above the configured threshold routes automatically.
- Confidence below the threshold sends the ticket to `NEEDS_REVIEW`.
- AI failure or timeout sends the ticket to `NEEDS_REVIEW` with `ai_routing_status = FAILED`.
- Staff and admins can override any AI suggestion. The effective value and the suggested value are stored in separate columns, and the override is written to `ticket_history`.
- `original_text` is retained for audit and future model improvement.

The confidence threshold is configuration, not a constant buried in code. Expect to tune it after seeing real submissions.

## 9. Departments

Seeded departments: Facilities, IT, Residence Life, Student Engagement, Campus Safety, Dining, Finance, and Other / Review.

Each issue type carries a default department, which provides deterministic routing when AI is unavailable or unconfident.

Each ticket has exactly one primary department. Departments are deactivated (`active = false`), never deleted, so existing tickets keep a valid reference.

## 10. Comments and Notes

Users and staff can comment. `comments.is_internal` marks staff-only operational notes, which are hidden from the requester by RLS rather than by frontend filtering.

## 11. Attachments

- Common image types only, validated by content type and extension.
- Enforced file-size limit.
- Every attachment belongs to a ticket and records its uploader.
- Files live in a **private** Supabase Storage bucket.
- Access is via short-lived signed URLs. There are no public object URLs.

## 12. Reopen Workflow

- The Reopen action is available to the requester for **seven days** after `resolved_at`.
- Reopening creates a `ticket_reopen_requests` row with `decision = 'PENDING'`.
- Only one pending request may exist per ticket.
- A Department Admin or System Admin approves or denies, with an optional reason.
- An approved request moves the ticket to `REOPENED` and back into the active queue.
- A denied request leaves the ticket resolved, and the decision is recorded.
- Every step writes to `ticket_history`.

### Post-resolution replies

A comment added by the requester after resolution **remains a comment** and does not implicitly reopen the ticket. It notifies the assigned staff member. Reopening requires the explicit Reopen action. This keeps "I have a follow-up question" distinct from "this was not fixed."

## 13. Emergency Handling

Submissions containing emergency language — fire, gas, flooding, injury, threat — must not enter the maintenance queue silently.

On detection the interface interrupts submission with a prominent warning directing the user to call emergency services or Campus Safety, with the number displayed. The user may still submit the ticket afterward for record-keeping, in which case it is flagged `is_emergency` and routed to Campus Safety at top priority.

Detection is keyword-based and runs **client-side at submission time**, not in the AI layer. It must work when AI is unavailable, and it must be instant.

## 14. Department Dashboard

Department staff see a queue with filters and sorting on status, priority, building, creation date, and assignment, plus ticket detail with comments, notes, and resolution controls.

## 15. Analytics Dashboard

Authorized administrators see ticket volume; tickets by department, category, and building; open vs. resolved; average resolution time; aging tickets; trends over time; recurring locations; and map-based ticket patterns.

Students never see campus-wide analytics.

## 16. Security and Privacy

- Authorization is enforced by RLS policies in the database. UI hiding is presentation, not security.
- A student manipulating a ticket ID in a URL receives no data, because the database refuses the row.
- Department-level access derives from `user_departments`.
- Attachments are private-bucket objects served through signed URLs.
- FastAPI uses the `service_role` key, which bypasses RLS, so every FastAPI endpoint re-checks authorization explicitly.
- Collect the minimum necessary information. Do not add sensitive fields without a stated reason.

## 17. Failure Handling

| Failure | Behavior |
|---|---|
| AI unavailable | Ticket created, routed to `NEEDS_REVIEW` |
| Notification delivery fails | Ticket change stays committed; notification marked `FAILED` and retried |
| Staff member leaves | Open tickets stay visible and reassignable |
| Department deactivated | Existing tickets remain intact and readable |
| Duplicate issue | Flagged for review; no automatic merging in the MVP |
| Storage upload fails | Ticket still created; attachment can be retried |

## 18. Non-Functional Requirements

- **Browsers**: current Chrome, Edge, Firefox, Safari. No IE.
- **Responsive**: the submission flow and ticket list work at phone width. Staff queue and analytics are desktop-first.
- **Performance**: ticket list and queue views respond in under one second with 1,000 seeded tickets; map renders 500 markers without freezing.
- **Accessibility**: keyboard-navigable forms, labeled inputs, and text contrast meeting WCAG AA.
- **Scale**: tens of concurrent users. This is a campus tool, not a public service.
