import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { useAuth } from '../auth/AuthProvider'
import { useLookups, nameById, statusNameById } from '../lib/useLookups'
import { Empty, ErrorNote, Loading, PriorityBadge, StatusBadge } from '../components/ui'
import { formatRelative } from '../lib/format'
import { OPEN_STATUSES } from '../lib/domain'
import type { Tables } from '../lib/database.types'

type SortKey = 'newest' | 'oldest' | 'priority'
type AssignFilter = '' | 'mine' | 'unassigned' | 'reopen'

export default function StaffQueuePage() {
  const { lookups, ready } = useLookups()
  const { profile } = useAuth()
  const [tickets, setTickets] = useState<Tables<'tickets'>[] | null>(null)
  const [error, setError] = useState<unknown>(null)

  const [status, setStatus] = useState('')
  const [department, setDepartment] = useState('')
  const [building, setBuilding] = useState('')
  const [priority, setPriority] = useState('')
  const [openOnly, setOpenOnly] = useState(true)
  const [sort, setSort] = useState<SortKey>('newest')
  const [query, setQuery] = useState('')
  const [assignFilter, setAssignFilter] = useState<AssignFilter>('')

  // ticket_id -> assignee name / id, and tickets with a pending reopen.
  const [assignees, setAssignees] = useState<Record<number, { id: string; name: string }>>({})
  const [pendingReopen, setPendingReopen] = useState<Set<number>>(new Set())

  // Building lives two joins away, so resolve ticket -> building once.
  const [ticketBuilding, setTicketBuilding] = useState<Record<number, string>>({})

  useEffect(() => {
    // RLS scopes this to the caller's departments. A system admin sees all;
    // department staff see only theirs. No client-side filter is required
    // for correctness — only for usability.
    supabase
      .from('tickets')
      .select('*')
      .is('deleted_at', null)
      .order('created_at', { ascending: false })
      .limit(500)
      .then(async ({ data, error }) => {
        if (error) {
          setError(error)
          return
        }
        setTickets(data)

        // Both are RLS-scoped the same way as the tickets themselves.
        void supabase
          .from('ticket_assignments')
          .select('ticket_id, users!ticket_assignments_assigned_to_fkey(user_id, first_name, last_name)')
          .is('unassigned_at', null)
          .then(({ data: rows }) => {
            const map: Record<number, { id: string; name: string }> = {}
            for (const r of rows ?? []) {
              const u = r.users as unknown as { user_id: string; first_name: string; last_name: string } | null
              if (u) map[r.ticket_id] = { id: u.user_id, name: `${u.first_name} ${u.last_name}` }
            }
            setAssignees(map)
          })
        void supabase
          .from('ticket_reopen_requests')
          .select('ticket_id')
          .eq('decision', 'PENDING')
          .then(({ data: rows }) => setPendingReopen(new Set((rows ?? []).map((r) => r.ticket_id))))

        const locationIds = [...new Set(data.map((t) => t.location_id).filter(Boolean))] as number[]
        if (locationIds.length === 0) return

        const { data: locs } = await supabase
          .from('locations')
          .select('location_id, floors(buildings(name))')
          .in('location_id', locationIds)

        const byLocation = new Map<number, string>()
        for (const l of locs ?? []) {
          const floor = l.floors as unknown as { buildings: { name: string } | null } | null
          if (floor?.buildings?.name) byLocation.set(l.location_id, floor.buildings.name)
        }
        const map: Record<number, string> = {}
        for (const t of data) {
          if (t.location_id && byLocation.has(t.location_id)) {
            map[t.ticket_id] = byLocation.get(t.location_id)!
          }
        }
        setTicketBuilding(map)
      })
  }, [])

  const visible = useMemo(() => {
    if (!tickets) return []
    const priorityLevel = new Map(lookups.priorities.map((p) => [p.priority_id, p.level]))

    const q = query.trim().toLowerCase()
    let rows = tickets.filter((t) => {
      const name = statusNameById(lookups, t.status_id)
      // A pending reopen sits on a RESOLVED ticket, so it bypasses open-only.
      if (assignFilter === 'reopen') return pendingReopen.has(t.ticket_id)
      if (openOnly && (!name || !OPEN_STATUSES.includes(name))) return false
      if (assignFilter === 'mine' && assignees[t.ticket_id]?.id !== profile?.user_id) return false
      if (assignFilter === 'unassigned' && assignees[t.ticket_id]) return false
      if (
        q &&
        !`#${t.ticket_id} ${t.title} ${t.description ?? t.original_text}`.toLowerCase().includes(q)
      )
        return false
      if (status && name !== status) return false
      if (department && String(t.department_id) !== department) return false
      if (priority && String(t.priority_id) !== priority) return false
      if (building && ticketBuilding[t.ticket_id] !== building) return false
      return true
    })

    rows = [...rows].sort((a, b) => {
      if (sort === 'priority') {
        const pa = priorityLevel.get(a.priority_id ?? -1) ?? 0
        const pb = priorityLevel.get(b.priority_id ?? -1) ?? 0
        if (pa !== pb) return pb - pa
      }
      const ta = new Date(a.created_at).getTime()
      const tb = new Date(b.created_at).getTime()
      return sort === 'oldest' ? ta - tb : tb - ta
    })

    // Emergencies float regardless of the chosen sort.
    return [...rows].sort((a, b) => Number(b.is_emergency) - Number(a.is_emergency))
  }, [tickets, lookups, status, department, priority, building, openOnly, sort, ticketBuilding,
      query, assignFilter, assignees, pendingReopen, profile])

  if (error) return <div className="content"><ErrorNote error={error} /></div>
  if (!tickets || !ready) return <Loading />

  // Every building, not only those that already have a located ticket.
  // Deriving this from tickets left the filter empty whenever nothing had a
  // location yet, which looked like the filter was broken.
  const buildingNames = lookups.buildings.map((b) => b.name)

  return (
    <div className="content">
      <h1>Department queue</h1>
      <p className="muted small">
        Showing {visible.length} of {tickets.length} tickets you have access to.
      </p>

      {pendingReopen.size > 0 && assignFilter !== 'reopen' && (
        <div className="alert warn">
          {pendingReopen.size} reopen request{pendingReopen.size === 1 ? '' : 's'} waiting for a
          decision.{' '}
          <button className="link" onClick={() => setAssignFilter('reopen')}>
            Show them
          </button>
        </div>
      )}

      <div className="card card-tight">
        <div className="row" style={{ marginBottom: '0.6rem' }}>
          <div style={{ flex: '3 1 240px' }}>
            <label htmlFor="f-q">Search</label>
            <input
              id="f-q"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Ticket number, title, or description"
            />
          </div>
          <div>
            <label htmlFor="f-assign">Assignment</label>
            <select
              id="f-assign"
              value={assignFilter}
              onChange={(e) => setAssignFilter(e.target.value as AssignFilter)}
            >
              <option value="">Anyone</option>
              <option value="mine">Assigned to me</option>
              <option value="unassigned">Unassigned</option>
              <option value="reopen">Reopen requested</option>
            </select>
          </div>
        </div>
        <div className="row">
          <div>
            <label htmlFor="f-status">Status</label>
            <select id="f-status" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">Any</option>
              {lookups.statuses.map((s) => (
                <option key={s.status_id} value={s.name}>{s.name.replace(/_/g, ' ')}</option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="f-dept">Department</label>
            <select id="f-dept" value={department} onChange={(e) => setDepartment(e.target.value)}>
              <option value="">Any</option>
              {lookups.departments.map((d) => (
                <option key={d.department_id} value={d.department_id}>{d.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="f-pri">Priority</label>
            <select id="f-pri" value={priority} onChange={(e) => setPriority(e.target.value)}>
              <option value="">Any</option>
              {lookups.priorities.map((p) => (
                <option key={p.priority_id} value={p.priority_id}>{p.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="f-bldg">Building</label>
            <select id="f-bldg" value={building} onChange={(e) => setBuilding(e.target.value)}>
              <option value="">Any</option>
              {buildingNames.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor="f-sort">Sort</label>
            <select id="f-sort" value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
              <option value="priority">Highest priority</option>
            </select>
          </div>
        </div>
        <label style={{ display: 'flex', gap: '0.4rem', alignItems: 'center', fontWeight: 400, marginTop: '0.5rem' }}>
          <input
            type="checkbox"
            checked={openOnly}
            onChange={(e) => setOpenOnly(e.target.checked)}
            style={{ width: 'auto' }}
          />
          Open tickets only
        </label>
      </div>

      {visible.length === 0 ? (
        <Empty title="Nothing matches these filters" hint="Try widening the status or department filter." />
      ) : (
        visible.map((t) => (
          <Link key={t.ticket_id} to={`/tickets/${t.ticket_id}`} className="ticket-row">
            <div className="title">
              {t.is_emergency && <span className="badge danger">Emergency</span>} #{t.ticket_id} · {t.title}
            </div>
            <div className="small muted">{t.description ?? t.original_text}</div>
            <div className="ticket-meta">
              <StatusBadge status={statusNameById(lookups, t.status_id)} />
              <PriorityBadge priority={nameById(lookups.priorities, 'priority_id', t.priority_id)} />
              <span className="badge">
                {nameById(lookups.departments, 'department_id', t.department_id) ?? 'Unrouted'}
              </span>
              {ticketBuilding[t.ticket_id] && <span className="badge">{ticketBuilding[t.ticket_id]}</span>}
              {pendingReopen.has(t.ticket_id) && <span className="badge warn">Reopen requested</span>}
              <span className="small muted">
                {assignees[t.ticket_id] ? assignees[t.ticket_id].name : 'Unassigned'} ·
              </span>
              <span className="small muted">{formatRelative(t.created_at)}</span>
            </div>
          </Link>
        ))
      )}
    </div>
  )
}
