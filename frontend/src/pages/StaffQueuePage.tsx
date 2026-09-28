import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { useLookups, nameById, statusNameById } from '../lib/useLookups'
import { Empty, ErrorNote, Loading, PriorityBadge, StatusBadge } from '../components/ui'
import { formatRelative } from '../lib/format'
import { OPEN_STATUSES } from '../lib/domain'
import type { Tables } from '../lib/database.types'

type SortKey = 'newest' | 'oldest' | 'priority'

export default function StaffQueuePage() {
  const { lookups, ready } = useLookups()
  const [tickets, setTickets] = useState<Tables<'tickets'>[] | null>(null)
  const [error, setError] = useState<unknown>(null)

  const [status, setStatus] = useState('')
  const [department, setDepartment] = useState('')
  const [building, setBuilding] = useState('')
  const [priority, setPriority] = useState('')
  const [openOnly, setOpenOnly] = useState(true)
  const [sort, setSort] = useState<SortKey>('newest')

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

    let rows = tickets.filter((t) => {
      const name = statusNameById(lookups, t.status_id)
      if (openOnly && (!name || !OPEN_STATUSES.includes(name))) return false
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
  }, [tickets, lookups, status, department, priority, building, openOnly, sort, ticketBuilding])

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

      <div className="card card-tight">
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
              <span className="small muted">{formatRelative(t.created_at)}</span>
            </div>
          </Link>
        ))
      )}
    </div>
  )
}
