import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { useAuth } from '../auth/AuthProvider'
import { useLookups, nameById, statusNameById } from '../lib/useLookups'
import { Empty, ErrorNote, Loading, PriorityBadge, StatusBadge } from '../components/ui'
import { formatRelative } from '../lib/format'
import type { Tables } from '../lib/database.types'

export default function MyTicketsPage() {
  const { profile } = useAuth()
  const { lookups, ready } = useLookups()
  const [tickets, setTickets] = useState<Tables<'tickets'>[] | null>(null)
  const [error, setError] = useState<unknown>(null)

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

  if (error) return <div className="content"><ErrorNote error={error} /></div>
  if (!tickets || !ready) return <Loading />

  return (
    <div className="content">
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '1rem',
          marginBottom: '1rem',
        }}
      >
        <h1 style={{ margin: 0 }}>My tickets</h1>
        <Link to="/submit">
          <button className="primary">Report an issue</button>
        </Link>
      </div>

      {tickets.length === 0 ? (
        <Empty
          title="You haven't reported anything yet"
          hint={<Link to="/submit">Report your first issue</Link>}
        />
      ) : (
        tickets.map((t) => {
          const status = statusNameById(lookups, t.status_id)
          return (
            <Link key={t.ticket_id} to={`/tickets/${t.ticket_id}`} className="ticket-row">
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
