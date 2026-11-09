import { useCallback, useEffect, useRef, useState } from 'react'
import { supabase } from '../lib/supabase'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNote } from './ui'
import { formatRelative } from '../lib/format'
import type { Tables } from '../lib/database.types'

interface MessageRow extends Tables<'ticket_messages'> {
  users: { first_name: string; last_name: string } | null
}

interface Props {
  ticketId: number
  requesterId: string
  /** Active assignee's user id, or null when the ticket is unassigned. */
  assigneeId: string | null
  closed: boolean
  /** Called after a message is sent, so a surrounding list can refresh. */
  onSent?: () => void
  /** Pause polling while the chat is not on screen. */
  active?: boolean
}

/**
 * The private requester <-> assigned-staff conversation for one ticket.
 *
 * Rules are enforced by RLS and the send_ticket_message RPC; this component
 * only decides what to offer. A requester cannot read staff profiles (RLS),
 * so staff names can be null and fall back to a role label.
 */
export function TicketChat({ ticketId, requesterId, assigneeId, closed, onSent, active = true }: Props) {
  const { profile } = useAuth()
  const [messages, setMessages] = useState<MessageRow[] | null>(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const endRef = useRef<HTMLDivElement>(null)

  const isOwner = profile?.user_id === requesterId
  const isAssignee = !!assigneeId && assigneeId === profile?.user_id
  const staffHasSpoken = (messages ?? []).some((m) => m.sender_id !== requesterId)
  const canSend = !closed && !!assigneeId && (isAssignee || (isOwner && staffHasSpoken))

  let note: string | null = null
  if (!canSend) {
    if (closed) note = 'This ticket is closed. Messaging is disabled.'
    else if (!assigneeId) note = 'Messaging starts once the ticket is assigned.'
    else if (isOwner) note = 'Your assigned staff member will message you here first.'
    else note = 'Read-only: only the assigned staff member and the requester can send messages.'
  }

  const load = useCallback(async (signal?: AbortSignal) => {
    let q = supabase
      .from('ticket_messages')
      .select('*, users(first_name, last_name)')
      .eq('ticket_id', ticketId)
      .order('created_at')
    if (signal) q = q.abortSignal(signal)
    const { data, error: err } = await q
    if (err) {
      if (!signal?.aborted) setError(err)
      return
    }
    setMessages((data ?? []) as MessageRow[])
  }, [ticketId])

  // Switching tickets must not flash the previous conversation.
  useEffect(() => {
    setMessages(null)
    setDraft('')
    setError(null)
    const controller = new AbortController()
    void load(controller.signal)
    return () => controller.abort()
  }, [load])

  // Light refresh so a reply shows up without a reload. Nothing can arrive on
  // a closed ticket, so stop there.
  useEffect(() => {
    if (!active || closed) return
    const controller = new AbortController()
    const timer = setInterval(() => void load(controller.signal).catch(() => undefined), 10000)
    return () => {
      clearInterval(timer)
      controller.abort()
    }
  }, [active, closed, load])

  const count = messages?.length ?? 0
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'nearest' })
  }, [count, ticketId])

  async function send() {
    const text = draft.trim()
    if (!text || busy) return
    setBusy(true)
    setError(null)
    const { error: err } = await supabase.rpc('send_ticket_message', {
      p_ticket_id: ticketId,
      p_message: text,
    })
    if (err) {
      setError(err)
    } else {
      setDraft('')
      await load()
      onSent?.()
    }
    setBusy(false)
  }

  return (
    <div className="chat-thread">
      <div className="chat-log">
        {messages === null && <p className="muted small">Loading messages…</p>}
        {messages?.length === 0 && <p className="muted small">No messages yet.</p>}
        {messages?.map((m) => {
          const mine = m.sender_id === profile?.user_id
          const label = mine
            ? 'You'
            : m.users
              ? `${m.users.first_name} ${m.users.last_name}`
              : isOwner ? 'Assigned staff' : 'Requester'
          return (
            <div key={m.message_id} className={`comment chat ${mine ? 'mine' : ''}`}>
              <div className="head">{label} · {formatRelative(m.created_at)}</div>
              <div style={{ whiteSpace: 'pre-wrap' }}>{m.message}</div>
            </div>
          )
        })}
        <div ref={endRef} />
      </div>

      {error != null && <ErrorNote error={error} />}

      {canSend ? (
        <>
          <div className="field" style={{ marginTop: '0.75rem' }}>
            <label htmlFor={`message-${ticketId}`}>Message</label>
            <textarea
              id={`message-${ticketId}`}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={isOwner ? 'Reply to your assigned staff member.' : 'Write to the requester.'}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) void send()
              }}
            />
          </div>
          <div className="actions">
            <button className="primary" disabled={busy || !draft.trim()} onClick={() => void send()}>
              Send
            </button>
          </div>
        </>
      ) : (
        <div className="hint" style={{ marginTop: '0.75rem' }}>{note}</div>
      )}
    </div>
  )
}
