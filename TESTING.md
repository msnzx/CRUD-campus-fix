# CampusFix — Manual Test Script

How to exercise the whole product by hand, and what each step actually proves.
IDs cross-reference `verification.md`.

## Test accounts

Created for local verification only. **Delete before any real deployment** —
see Teardown at the bottom.

| Email | Role | Department | Password |
|---|---|---|---|
| *your own account* | SYSTEM_ADMIN | all | *yours* |
| `fac.admin@caldwell.edu` | DEPARTMENT_ADMIN | Facilities | `TestPass123!` |
| `fac.staff@caldwell.edu` | DEPARTMENT_STAFF | Facilities | `TestPass123!` |
| `it.staff@caldwell.edu` | DEPARTMENT_STAFF | IT | `TestPass123!` |
| `student2@caldwell.edu` | STUDENT | — | `TestPass123!` |

Use a private window for the second account so two sessions can be open at once.

---

## 1. Student submission — V-001

As **student2**: Report an issue, with a title, a description of 20+
characters, category **Plumbing**, and a building and floor. Submit.

**Expect:** you land on the ticket detail page. Department reads
**Facilities**, status **New**, and History already has a `Created` entry.

**Proves:** deterministic routing works with AI switched off entirely. The
`default_department_id` on the Plumbing issue type did that, not a model.

**1b. Minimum information (V-004).** Submit with a 5-character description.
The button stays disabled and the hint explains why.

**1c. Attachment validation.** Attach a `.pdf`, or an image over 10 MB.
Rejected before upload, with a readable message.

---

## 2. Emergency detection — V-005

As **student2**, title a report "Smoke in the stairwell" and describe smoke
coming from a stairwell.

**Expect:** a red banner appears *as you type*, before you submit. Submit
raises an interstitial naming 911 and Campus Safety at (973) 618-3259.
Choosing "I've called — file the report" creates the ticket flagged
**Emergency**, routed to **Campus Safety**, priority **Urgent**.

**Proves:** emergency handling is client-side and instant. Stop the backend
and repeat — behaviour is identical. That is the entire point of doing this
outside the AI layer.

---

## 3. Cross-user isolation — V-003

The headline security requirement.

1. As **student2**, note your ticket number from the URL, e.g. `/tickets/4`.
2. Sign in as **it.staff** — IT department, not Facilities.
3. Navigate directly to `/tickets/4`.

**Expect:** "Ticket not available."

**Proves:** this is not a hidden button. The database returned zero rows.
Verify at the SQL level:

```sql
begin;
set local role authenticated;
set local request.jwt.claims = '{"sub":"<it.staff user_id>","role":"authenticated"}';
select ticket_id from public.tickets;   -- the Facilities ticket is absent
rollback;
```

Repeat as **fac.staff**: the same ticket *is* visible, because it belongs to
their department.

---

## 4. Staff workflow — V-020 through V-023

As **fac.staff**:

1. **Queue** lists the Plumbing ticket. Emergencies sort to the top whatever
   sort order is selected.
2. Filter by status, priority, building — the count updates.
3. Open the ticket, **Claim** it. Status becomes Assigned and History records it.
4. **Move to In Progress**.
5. Add a public comment, then tick **Internal note** and add a second.
6. **Resolve** with resolution notes.

**Expect:** every step appends to History with an actor and a timestamp.

**4b. Internal notes really are hidden (V-002).** Sign back in as **student2**
and open the same ticket. The public comment is there; the internal note is
not. It is filtered by RLS, not by CSS — check the network response if you
want to be certain.

---

## 5. Reopen flow — V-024, V-025, V-026

As **student2**, on the resolved ticket:

1. **Request reopen**, with a reason.
2. Try again — blocked. One pending request per ticket, enforced by a partial
   unique index.
3. Sign in as **fac.admin**: the pending request appears with Approve / Deny.
4. Approve. The ticket returns to **Reopened** and re-enters the queue.

**fac.staff** does not see Approve/Deny. That is a DEPARTMENT_ADMIN action by
design — `verification.md` §8.

**5b. A comment does not reopen (V-026).** On a resolved ticket, comment as the
requester. Status does not change; the placeholder text says so.

**5c. Window expiry (V-025).**

```sql
update public.tickets set resolved_at = now() - interval '8 days'
 where ticket_id = <id>;
```

Reload: the Reopen button is gone and the hint explains why. The RLS policy
also rejects a direct insert, so a crafted request cannot get around it.

---

## 6. Withdraw

As **student2**, submit a fresh ticket and **Withdraw** it. It leaves your list.

Now have staff move a ticket to In Progress and try to withdraw that one. The
RPC refuses: work has begun. Requesters have no UPDATE policy on tickets at
all — withdrawal exists only through `withdraw_own_ticket()`, which can set
nothing but `deleted_at`.

---

## 7. Analytics

Compare Analytics as **fac.staff** against your **SYSTEM_ADMIN** account.

**Expect:** staff see Facilities figures only; the system admin sees every
department. Same page, same query, different rows — RLS filters the underlying
tickets, so the chart code never needs to know about permissions.

Students have no Analytics tab, and `/analytics` redirects them away.

---

## 8. Privilege escalation — all of these must fail

```sql
begin;
set local role authenticated;
set local request.jwt.claims = '{"sub":"<student2 id>","role":"authenticated"}';

-- Promote self: blocked by a column-level grant
update public.users set role_id = 4 where user_id = '<student2 id>';

-- Edit own ticket directly: 0 rows, because no UPDATE policy applies
update public.tickets set title = 'hijacked' where reported_by = '<student2 id>';

rollback;
```

Signed out, as `anon`, every table returns permission denied.

Also run the Supabase security advisors after any migration. The only expected
finding is `withdraw_own_ticket` being callable by authenticated users — that
is its purpose, and it checks ownership and status internally.

---

## 9. AI classification

**Without `ANTHROPIC_API_KEY`:** submit a ticket leaving category blank. The
backend marks it `FAILED` and routes to **Needs Review**. The ticket exists and
the user sees no error. This is V-012 and it is a release gate, not an optional
check.

**With a key:** the same ticket gains a suggested department, category,
priority and confidence. At or above 0.75 it auto-routes; below, Needs Review.
A staff override is stored separately from the suggestion, so the original
recommendation survives for auditing (V-010, V-011, V-013).

Stop the backend mid-test and submit again. Ticket creation is unaffected.

---

## Teardown

```sql
delete from public.ticket_history where ticket_id in (
  select ticket_id from public.tickets where reported_by in (
    select user_id from public.users where email in (
      'student2@caldwell.edu','fac.staff@caldwell.edu',
      'fac.admin@caldwell.edu','it.staff@caldwell.edu')));

delete from public.tickets where reported_by in (
  select user_id from public.users where email in (
    'student2@caldwell.edu','fac.staff@caldwell.edu',
    'fac.admin@caldwell.edu','it.staff@caldwell.edu'));

delete from auth.users where email in (
  'student2@caldwell.edu','fac.staff@caldwell.edu',
  'fac.admin@caldwell.edu','it.staff@caldwell.edu');
```

## If you create test users by hand again

These accounts were inserted straight into `auth.users`. If you do that again,
set `confirmation_token`, `recovery_token`, `email_change_token_new` and
`email_change` to `''` rather than leaving them NULL, and insert a matching
`auth.identities` row. Otherwise sign-in fails with an opaque
`500 Database error querying schema`, because GoTrue cannot scan NULL into a
string field.

Signing up through the UI avoids all of this and is the better default.
