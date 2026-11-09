import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { useAuth } from '../auth/AuthProvider'
import { Empty, ErrorNote, Loading, StatusBadge } from '../components/ui'
import { TicketChat } from '../components/TicketChat'
import { formatRelative } from '../lib/format'
import { STATUS } from '../lib/domain'
import type { Database } from '../lib/database.types'

type Thread = Database['public']['Functions']['my_message_threads']['Returns'][number]

/**
 * Every private chat the signed-in user can see, in one place. The list comes
 * from my_message_threads(), which runs under the caller's RLS, so each role
 * gets its own view: requesters their tickets, assignees the tickets they are
 * working, admins their departments' chats (read-only).
 */
export default function MessagesPage() {
  const { id } = useParams()
  const selectedId = id ? Number(id) : null
  const nav = useNavigate()
  const { profile } = useAuth()

  const [threads, setThreads] = useState<Thread[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [query, setQuery] = useState('')

  const load = useCallback(async () => {
    const { data, error: err } = await supabase.rpc('my_message_threads')
    if (err) setError(err)
    else {
      setError(null)
      setThreads(data ?? [])
    }
  }, [])

  useEffect(() => {
    void load()
    // Pick up new messages and newly assigned tickets without a reload.
    const timer = setInterval(() => void load(), 15000)
    return () => clearInterval(timer)
  }, [load])

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return threads ?? []
    return (threads ?? []).filter((t) =>
      `#${t.ticket_id} ${t.title} ${t.requester_name ?? ''} ${t.last_message ?? ''}`
        .toLowerCase()
        .includes(q),
    )
  }, [threads, query])

  const selected = threads?.find((t) => t.ticket_id === selectedId) ?? null

  if (error && !threads) return <div className="content"><ErrorNote error={error} /></div>
  if (!threads) return <Loading />

  function partyLabel(t: Thread): string {
    if (t.role === 'REQUESTER') return 'Assigned staff'
    return t.requester_name ?? 'Requester'
  }

  function preview(t: Thread): string {
    if (!t.last_message) {
      if (t.role === 'ASSIGNEE') return 'No messages yet. Start the conversation.'
      if (t.role === 'REQUESTER') return 'Waiting for your assigned staff member to write first.'
      return 'No messages yet.'
    }
    const prefix = t.last_sender === profile?.user_id ? 'You: ' : ''
    return prefix + t.last_message
  }

  return (
    <div className="content">
      <h1 style={{ marginTop: 0 }}>Messages</h1>

      {error != null && <ErrorNote error={error} />}

      {threads.length === 0 ? (
        <Empty
          title="No conversations yet"
          hint="Once a ticket is assigned, the staff member handling it can message you here."
        />
      ) : (
        <div className={`messages-layout ${selected ? 'has-selection' : ''}`}>
          <aside className="messages-list card card-tight">
            <input
              aria-label="Search conversations"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by ticket, title, or person"
            />
            {visible.length === 0 && <p className="muted small">No conversations match.</p>}
            <ul>
              {visible.map((t) => (
                <li key={t.ticket_id}>
                  <button
                    type="button"
                    className={`thread ${t.ticket_id === selectedId ? 'selected' : ''}`}
                    onClick={() => nav(`/messages/${t.ticket_id}`)}
                  >
                    <div className="thread-top">
                      <strong>{partyLabel(t)}</strong>
                      <span className="muted small">{t.last_at ? formatRelative(t.last_at) : ''}</span>
                    </div>
                    <div className="small">#{t.ticket_id} · {t.title}</div>
                    <div className="thread-preview muted small">{preview(t)}</div>
                    <div className="thread-meta">
                      <StatusBadge status={t.status} />
                      {t.role === 'OBSERVER' && <span className="badge">View only</span>}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </aside>

          <section className="messages-pane card">
            {selected ? (
              <>
                <div className="messages-pane-head">
                  <button type="button" className="sm back-to-list" onClick={() => nav('/messages')}>
                    ← All messages
                  </button>
                  <h3 style={{ margin: 0 }}>
                    #{selected.ticket_id} · {selected.title}
                  </h3>
                  <div className="small muted">
                    {selected.role === 'REQUESTER'
                      ? 'Private conversation with the staff member handling your ticket.'
                      : `Private conversation with ${selected.requester_name ?? 'the requester'}.`}{' '}
                    <Link to={`/tickets/${selected.ticket_id}`}>Open ticket</Link>
                  </div>
                </div>
                <TicketChat
                  ticketId={selected.ticket_id}
                  requesterId={selected.reported_by}
                  assigneeId={selected.assigned_to}
                  closed={selected.status === STATUS.CLOSED}
                  onSent={() => void load()}
                />
              </>
            ) : (
              <p className="muted">
                {selectedId
                  ? 'That conversation is not available. It may be closed, reassigned, or not yours.'
                  : 'Select a conversation to read it.'}
              </p>
            )}
          </section>
        </div>
      )}
    </div>
  )
}
