import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { useAuth } from '../auth/AuthProvider'
import { useLookups, nameById, statusNameById } from '../lib/useLookups'
import { Empty, ErrorNote, Loading, PriorityBadge, StatusBadge } from '../components/ui'
import { formatRelative } from '../lib/format'
import { OPEN_STATUSES } from '../lib/domain'
import type { Tables } from '../lib/database.types'

export default function MyTicketsPage() {
  const { profile } = useAuth()
  const { lookups, ready } = useLookups()
  const [tickets, setTickets] = useState<Tables<'tickets'>[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [query, setQuery] = useState('')
  const [show, setShow] = useState<'open' | 'done' | 'all'>('all')

  useEffect(() => {
    if (!profile) return
    // RLS already limits this to the caller's own rows. The explicit filter
    // keeps intent obvious and stops staff seeing their department's queue
    // on what is meant to be a personal list.
    supabase
      .from('tickets')
      .select('*')
      .eq('reported_by', profile.user_id)
      .is('deleted_at', null)
      .order('created_at', { ascending: false })
      .then(({ data, error }) => {
        if (error) setError(error)
        else setTickets(data)
      })
  }, [profile])

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (tickets ?? []).filter((t) => {
      const name = statusNameById(lookups, t.status_id)
      const open = !!name && OPEN_STATUSES.includes(name)
      if (show === 'open' && !open) return false
      if (show === 'done' && open) return false
      if (!q) return true
      return `#${t.ticket_id} ${t.title} ${t.description ?? t.original_text}`
        .toLowerCase()
        .includes(q)
    })
  }, [tickets, lookups, query, show])

  if (error) return <div className="content"><ErrorNote error={error} /></div>
  if (!tickets || !ready) return <Loading />

  const openCount = tickets.filter((t) => {
    const name = statusNameById(lookups, t.status_id)
    return !!name && OPEN_STATUSES.includes(name)
  }).length
  const resolvedCount = tickets.length - openCount

  return (
    <div className="content student-dashboard">
      <section className="dashboard-hero">
        <div>
          <p className="eyebrow">Student workspace</p>
          <h1>Keep campus moving.</h1>
          <p>Report an issue, follow the response, and see exactly what happens next.</p>
        </div>
        <Link className="primary dashboard-cta" to="/submit"><span aria-hidden="true">＋</span> Report an issue</Link>
      </section>

      <section className="ticket-summary" aria-label="Your ticket summary">
        <div className="summary-card"><span className="summary-icon" aria-hidden="true">◌</span><div><strong>{tickets.length}</strong><span>Total reports</span></div></div>
        <div className="summary-card"><span className="summary-icon warm" aria-hidden="true">↗</span><div><strong>{openCount}</strong><span>In progress</span></div></div>
        <div className="summary-card"><span className="summary-icon good" aria-hidden="true">✓</span><div><strong>{resolvedCount}</strong><span>Resolved</span></div></div>
      </section>

      {tickets.length > 0 && (
        <div className="card card-tight ticket-controls">
          <div className="section-label"><strong>Your reports</strong><span>Search or filter your activity</span></div>
          <div className="row">
            <div style={{ flex: '3 1 220px' }}>
              <label htmlFor="my-q">Search</label>
              <input
                id="my-q"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Ticket number, title, or description"
              />
            </div>
            <div>
              <label htmlFor="my-show">Show</label>
              <select id="my-show" value={show} onChange={(e) => setShow(e.target.value as typeof show)}>
                <option value="all">All tickets</option>
                <option value="open">Open</option>
                <option value="done">Resolved and closed</option>
              </select>
            </div>
          </div>
        </div>
      )}

      {tickets.length === 0 ? (
        <div className="empty-state-card"><span aria-hidden="true">⌁</span><Empty title="You haven't reported anything yet" hint={<Link to="/submit">Report your first issue</Link>} /></div>
      ) : visible.length === 0 ? (
        <Empty title="No tickets match" hint="Try a different search or show all tickets." />
      ) : (
        visible.map((t) => {
          const status = statusNameById(lookups, t.status_id)
          return (
            <Link key={t.ticket_id} to={`/tickets/${t.ticket_id}`} className="ticket-row redesigned-ticket-row">
              <div className="title">
                {t.is_emergency && <span className="badge danger">Emergency</span>}{' '}
                #{t.ticket_id} · {t.title}
              </div>
              <div className="small muted">{t.description ?? t.original_text}</div>
              <div className="ticket-meta">
                <StatusBadge status={status} />
                <PriorityBadge
                  priority={nameById(lookups.priorities, 'priority_id', t.priority_id)}
                />
                <span className="badge">
                  {nameById(lookups.departments, 'department_id', t.department_id) ??
                    'Awaiting routing'}
                </span>
                <span className="small muted">{formatRelative(t.created_at)}</span>
              </div>
            </Link>
          )
        })
      )}
    </div>
  )
}
