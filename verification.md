# CampusFix — Verification Plan

## 1. Purpose

Verification confirms that CampusFix satisfies its product requirements, database rules, security requirements, workflow rules, and major edge cases.

Every case below is written so it can be executed. Where the original plan left an option open, this document now states a decision, because a test cannot assert "optional."

## 2. Core User Flow

### V-001 — Submit Ticket

**Given** an authenticated student

**When** they submit a valid title, description, location, and optional attachment

**Then**
- the ticket is created
- `reported_by` matches the submitter
- `original_text` preserves the submitted text
- status is `NEW` and `ai_routing_status` is `PENDING`
- `created_at` is populated
- the ticket appears in the user's ticket list

The ticket is committed at `NEW`. It transitions to `AI_PROCESSING` only when classification is attempted, which may be after the user has already received confirmation.

### V-002 — View Own Ticket

**Given** a user who owns a ticket

**Then** they can view its details, status, history, public comments, and attachments.

They must **not** see comments where `is_internal = true`.

### V-003 — Prevent Cross-User Access

**Given** Student A knows Student B's ticket id

**When** Student A requests that ticket

**Then** the request returns no data.

Test this at the database level with Student A's JWT, not only through the UI. The RLS policy must be what denies the row. Repeat for comments, attachments, and history belonging to the other student's ticket.

### V-004 — Minimum Information

A submission with an empty or trivially short description, or with no location, is rejected with a message explaining what is missing.

### V-005 — Emergency Detection

**Given** a submission containing emergency language such as "fire in residence hall"

**Then** a warning interstitial appears before submission, directing the user to emergency services or Campus Safety with the number shown.

If the user proceeds, the ticket is flagged `is_emergency`, routed to Campus Safety, and given top priority.

This must work with the AI service disabled.

## 3. AI Verification

### V-010 — High Confidence

**Given** confidence at or above the threshold

**Then** the suggested department, issue type, and priority are stored; the ticket is routed; `ai_routing_status` is `AUTO_ROUTED`; and the decision appears in `ticket_history`.

### V-011 — Low Confidence

**Given** confidence below the threshold

**Then** the ticket is not routed automatically, `ai_routing_status` is `NEEDS_REVIEW`, status is `NEEDS_REVIEW`, and authorized staff can select the correct department.

### V-012 — AI Failure

**Given** the AI service is unavailable or times out

**Then** ticket creation still succeeds, `ai_routing_status` is `FAILED`, the ticket enters `NEEDS_REVIEW`, and the user sees no error.

Run the full submission flow with the AI service stopped. This is a release gate, not an optional test.

### V-013 — Override

**Given** an AI-suggested priority or department

**When** authorized staff changes it

**Then** the manual value becomes effective, the `ai_suggested_*` column retains the original suggestion, `ai_routing_status` becomes `OVERRIDDEN`, and the change is in `ticket_history`.

## 4. Ticket Workflow

### V-020 — Assignment

A staff member can be assigned, and the assignment is recorded in `ticket_assignments`.

### V-021 — Reassignment

An authorized user can reassign an open ticket. The prior assignment row is closed with `unassigned_at` rather than deleted. The partial unique index permits exactly one active assignment.

### V-022 — Status Transitions

Verify the legal path:

`NEW → AI_PROCESSING → ASSIGNED → IN_PROGRESS → RESOLVED → CLOSED`

and:

`IN_PROGRESS → WAITING_FOR_USER → IN_PROGRESS`

and:

`AI_PROCESSING → NEEDS_REVIEW → ASSIGNED`

Also verify that **illegal** transitions are rejected, for example `NEW → RESOLVED` and `CLOSED → IN_PROGRESS`.

### V-023 — Resolution

On resolve: `resolved_at` is populated, resolution notes are stored, the requester is notified, and history records the change.

### V-024 — Reopen

**Given** a ticket resolved within the last seven days

1. The requester sees a Reopen action.
2. Reopening creates a `PENDING` row in `ticket_reopen_requests`.
3. A second reopen attempt while one is pending is rejected by the partial unique index.
4. A Department Admin or System Admin can approve or deny, with an optional reason.
5. The decision, decider, and timestamp are recorded, and the CHECK constraint enforces that a decided row has both.
6. Approval moves the ticket to `REOPENED` and back into the active queue.
7. Denial leaves the ticket resolved.

### V-025 — Reopen Window Expiry

**Given** a ticket resolved more than seven days ago

**Then** the Reopen action is unavailable, and a reopen request submitted directly is rejected server-side.

### V-026 — Post-Resolution Reply

**Given** a resolved ticket

**When** the requester adds a comment

**Then** the comment is stored, the assigned staff member is notified, and the ticket status is **unchanged**. A comment never implicitly reopens a ticket.

## 5. Data Verification

### V-030 — Location Integrity

Verify:
- every floor references a valid building
- every non-null `locations.floor_id` references a valid floor
- tickets reference valid locations
- latitude is within −90 to 90 and longitude within −180 to 180

`locations.floor_id` is intentionally nullable, for outdoor and building-level locations such as a parking lot or a campus walkway. The test asserts referential validity when present, not presence.

### V-031 — History Integrity

Every important change creates a `ticket_history` row: status change, priority change, department transfer, assignment, unassignment, AI classification, AI override, reopen request, reopen decision, resolution, and closure.

### V-032 — Notification Integrity

**Given** notification delivery fails

**Then**
- the underlying ticket change remains committed
- the notification row records `delivery_status = 'FAILED'`, an incremented `attempts`, and an error message
- the notification is eligible for retry
- the ticket is not marked failed and the user-facing change is visible

### V-033 — Updated Timestamps

Updating a row bumps its `updated_at`. This verifies the trigger, not just the column default.

## 6. Attachments

Test: accepted image types; rejected non-image types; oversized files; missing file; a file whose extension and content type disagree; access by a user who does not own the ticket; a signed URL after expiry; and a database row whose storage object is missing.

No attachment may be reachable through a public, unsigned URL.

## 7. Edge Cases

**Wrong department.** Staff can transfer the ticket to the correct department, and the transfer is recorded.

**Multiple departments.** The MVP assigns one primary department. Verify the UI offers no multi-select.

**Duplicate tickets.** Duplicates can be flagged for review. Automatic merging is out of scope.

**Sensitive information.** Verify no unnecessary sensitive fields are collected, and that internal notes are not readable by requesters.

**Staff departure.** Deactivate a staff user with open assigned tickets. Verify the tickets remain visible to the department and can be reassigned.

**Department deactivation.** Set a department to `active = false`. Verify existing tickets remain readable and workable, and that the department no longer appears as a routing target for new tickets.

## 8. Authorization Matrix

Every cell is a test. There are no optional cells.

| Action | Student | Dept Staff | Dept Admin | System Admin |
|---|:--:|:--:|:--:|:--:|
| Create ticket | Yes | Yes | Yes | Yes |
| View own ticket | Yes | Yes | Yes | Yes |
| View other user's ticket | No | No | No | Yes |
| View department tickets | No | Yes | Yes | Yes |
| View all tickets | No | No | No | Yes |
| Assign ticket | No | Yes | Yes | Yes |
| Transfer department | No | **No** | Yes | Yes |
| Change status | No | Yes | Yes | Yes |
| Add public comment | Yes | Yes | Yes | Yes |
| Add internal note | No | Yes | Yes | Yes |
| Read internal notes | No | Yes | Yes | Yes |
| Resolve | No | Yes | Yes | Yes |
| Request reopen | Yes | No | No | No |
| Accept/deny reopen | No | No | Yes | Yes |
| View department analytics | No | **Yes** | Yes | Yes |
| View system analytics | No | No | No | Yes |
| Manage users | No | No | No | Yes |
| Manage department staff | No | No | Yes | Yes |

Two decisions resolved from the original draft:

- **Transfer department — staff: No.** Transferring moves a ticket out of the department's accountability. That is an admin action.
- **Department analytics — staff: Yes.** Staff can see metrics for their own department only. It costs nothing extra once the RLS policy exists and it makes the queue more useful.

Each row is tested twice: once that the permitted roles succeed, and once that the denied roles are refused **by the database**.

## 9. Database Verification

Verify foreign keys for: `tickets → users`, `tickets → departments`, `tickets → issue_types`, `tickets → priorities`, `tickets → statuses`, `tickets → locations`, `comments → tickets/users`, `attachments → tickets/users`, `ticket_assignments → tickets/users`, `ticket_history → tickets/users`, `ticket_reopen_requests → tickets/users`, `notifications → users/tickets`, `user_departments → users/departments`.

Verify unique constraints on: user email, role name, status name, priority name, priority level, issue type name, building name, area type name, department name, and the `(building_id, floor_number)` and `(user_id, department_id)` pairs.

Verify CHECK constraints: priority level between 1 and 4; `ai_confidence` between 0 and 1; `ai_routing_status` within its allowed set; reopen `decision` within its allowed set and consistent with `decided_by`/`decided_at`; non-empty `title` and `original_text`; positive `file_size_bytes`.

Verify the partial unique indexes: one active assignment per ticket, one pending reopen request per ticket.

### V-090 — RLS Coverage

Every table in `public` has RLS enabled and at least one policy. Query `pg_tables` and `pg_policies` to assert this, so a newly added table cannot silently ship world-readable.

## 10. Performance Smoke Tests

With the seeded demo dataset — several hundred tickets, multiple buildings and departments, repeated issue types, historical status changes:

- The student ticket list and staff queue respond in under one second.
- The analytics dashboard loads in under three seconds.
- The map renders its markers without freezing the browser.
- No query returns rows the current role is not permitted to see.

## 11. Final Acceptance

CampusFix is ready to demonstrate when:

- Student submission works from a phone.
- Staff and admin workflows work end to end.
- **RLS denies cross-user access, verified directly against the database.**
- AI failure does not block ticket creation, verified with the service switched off.
- Low-confidence classification reaches review.
- Assignments and history are complete and correct.
- Notifications deliver, or fail without corrupting ticket state.
- The reopen workflow works, including window expiry and denial.
- Emergency detection warns the user without depending on AI.
- The map and analytics work for authorized roles and are invisible to students.
- Every edge case in §7 is covered.
- The deployment is stable and the offline backup demo exists.
