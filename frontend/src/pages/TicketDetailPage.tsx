import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { supabase, ATTACHMENT_BUCKET } from '../lib/supabase'
import { useAuth } from '../auth/AuthProvider'
import { nameById, statusIdByName, statusNameById, useLookups } from '../lib/useLookups'
import { Empty, ErrorNote, Loading, Modal, PriorityBadge, Spinner, StatusBadge } from '../components/ui'
import { daysSince, formatDateTime, formatRelative, humanize } from '../lib/format'
import {
  ALLOWED_TRANSITIONS,
  REOPEN_WINDOW_DAYS,
  STATUS,
  WITHDRAWABLE,
} from '../lib/domain'
import type { Tables } from '../lib/database.types'

interface CommentRow extends Tables<'comments'> {
  users: { first_name: string; last_name: string } | null
}
interface HistoryRow extends Tables<'ticket_history'> {
  users: { first_name: string; last_name: string } | null
}

export default function TicketDetailPage() {
  const { id } = useParams()
  const ticketId = Number(id)
  const nav = useNavigate()
  const { profile, isStaff, isAdmin } = useAuth()
  const { lookups, ready } = useLookups()

  const [ticket, setTicket] = useState<Tables<'tickets'> | null>(null)
  const [comments, setComments] = useState<CommentRow[]>([])
  const [history, setHistory] = useState<HistoryRow[]>([])
  const [attachments, setAttachments] = useState<Tables<'attachments'>[]>([])
  const [signedUrls, setSignedUrls] = useState<Record<number, string>>({})
  const [reopenReq, setReopenReq] = useState<Tables<'ticket_reopen_requests'> | null>(null)

  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  const [draft, setDraft] = useState('')
  const [internal, setInternal] = useState(false)
  const [resolution, setResolution] = useState('')
  const [showResolve, setShowResolve] = useState(false)
  const [showReopen, setShowReopen] = useState(false)
  const [reopenReason, setReopenReason] = useState('')

  const load = useCallback(async () => {
    const { data: t, error: tErr } = await supabase
      .from('tickets')
      .select('*')
      .eq('ticket_id', ticketId)
      .maybeSingle()

    if (tErr) {
      setError(tErr)
      setLoading(false)
      return
    }
    // RLS returns nothing rather than an error for another user's ticket.
    // An empty result here IS the access denial.
    if (!t) {
      setNotFound(true)
      setLoading(false)
      return
    }
    setTicket(t)

    const [c, h, a, r] = await Promise.all([
      supabase
        .from('comments')
        .select('*, users(first_name, last_name)')
        .eq('ticket_id', ticketId)
        .order('created_at'),
      supabase
        .from('ticket_history')
        .select('*, users(first_name, last_name)')
        .eq('ticket_id', ticketId)
        .order('created_at', { ascending: false }),
      supabase.from('attachments').select('*').eq('ticket_id', ticketId),
      supabase
        .from('ticket_reopen_requests')
        .select('*')
        .eq('ticket_id', ticketId)
        .order('requested_at', { ascending: false })
        .limit(1)
        .maybeSingle(),
    ])

    setComments((c.data ?? []) as CommentRow[])
    setHistory((h.data ?? []) as HistoryRow[])
    setAttachments(a.data ?? [])
    setReopenReq(r.data ?? null)

    // Attachments live in a private bucket. Mint a short-lived signed URL
    // per object; there is no public path to these files.
    // One round trip per attachment, in parallel rather than in series.
    const attachmentRows = a.data ?? []
    const signed = await Promise.all(
      attachmentRows.map((att) =>
        supabase.storage
          .from(ATTACHMENT_BUCKET)
          .createSignedUrl(att.file_url, 300)
          .then((r) => [att.attachment_id, r.data?.signedUrl] as const),
      ),
    )
    const urls: Record<number, string> = {}
    for (const [id, url] of signed) if (url) urls[id] = url
    setSignedUrls(urls)
    setLoading(false)
  }, [ticketId])

  useEffect(() => {
    void load()
  }, [load])

  const statusName = useMemo(
    () => (ticket ? statusNameById(lookups, ticket.status_id) : null),
    [ticket, lookups],
  )

  const isOwner = !!ticket && ticket.reported_by === profile?.user_id
  const canWithdraw = isOwner && !!statusName && WITHDRAWABLE.includes(statusName)

  const resolvedDays = ticket?.resolved_at ? daysSince(ticket.resolved_at) : null
  const canReopen =
    isOwner &&
    statusName === STATUS.RESOLVED &&
    resolvedDays !== null &&
    resolvedDays <= REOPEN_WINDOW_DAYS &&
    reopenReq?.decision !== 'PENDING'

  const transitions = statusName ? (ALLOWED_TRANSITIONS[statusName] ?? []) : []

  // Supabase query builders are thenable but are not real Promises, so the
  // callback is typed as PromiseLike rather than Promise.
  async function run(fn: () => PromiseLike<unknown> | unknown) {
    setBusy(true)
    setError(null)
    try {
      const result = (await fn()) as { error?: unknown } | undefined
      if (result && typeof result === 'object' && 'error' in result && result.error) {
        throw result.error
      }
      await load()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  async function addComment() {
    if (!draft.trim() || !profile) return
    await run(async () => {
      const res = await supabase.from('comments').insert({
        ticket_id: ticketId,
        user_id: profile.user_id,
        message: draft.trim(),
        is_internal: internal,
      })
      setDraft('')
      return res
    })
  }

  async function changeStatus(to: string) {
    const statusId = statusIdByName(lookups, to)
    if (!statusId) return
    await run(() =>
      supabase.from('tickets').update({ status_id: statusId }).eq('ticket_id', ticketId),
    )
  }

  async function resolveTicket() {
    const statusId = statusIdByName(lookups, STATUS.RESOLVED)
    if (!statusId) return
    await run(async () => {
      const res = await supabase
        .from('tickets')
        .update({
          status_id: statusId,
          resolution_notes: resolution.trim() || null,
          resolved_at: new Date().toISOString(),
        })
        .eq('ticket_id', ticketId)
      setShowResolve(false)
      setResolution('')
      return res
    })
  }

  async function claim() {
    if (!profile) return
    await run(async () => {
      const assign = await supabase.from('ticket_assignments').insert({
        ticket_id: ticketId,
        assigned_to: profile.user_id,
        assigned_by: profile.user_id,
      })
      if (assign.error) return assign
      const statusId = statusIdByName(lookups, STATUS.ASSIGNED)
      return statusId
        ? supabase.from('tickets').update({ status_id: statusId }).eq('ticket_id', ticketId)
        : undefined
    })
  }

  async function withdraw() {
    await run(async () => {
      const res = await supabase.rpc('withdraw_own_ticket', { p_ticket_id: ticketId })
      if (!res.error) nav('/tickets')
      return res
    })
  }

  async function requestReopen() {
    if (!profile) return
    await run(async () => {
      const res = await supabase.from('ticket_reopen_requests').insert({
        ticket_id: ticketId,
        requested_by: profile.user_id,
        reason: reopenReason.trim() || null,
      })
      setShowReopen(false)
      setReopenReason('')
      return res
    })
  }

  async function decideReopen(decision: 'APPROVED' | 'DENIED') {
    if (!reopenReq || !profile) return
    await run(async () => {
      const res = await supabase
        .from('ticket_reopen_requests')
        .update({
          decision,
          decided_by: profile.user_id,
          decided_at: new Date().toISOString(),
        })
        .eq('request_id', reopenReq.request_id)
      if (res.error) return res
      if (decision === 'APPROVED') {
        const statusId = statusIdByName(lookups, STATUS.REOPENED)
        if (statusId) {
          return supabase
            .from('tickets')
            .update({ status_id: statusId, resolved_at: null })
            .eq('ticket_id', ticketId)
        }
      }
      return res
    })
  }

  if (loading || !ready) return <Loading />

  if (notFound) {
    return (
      <div className="content narrow">
        <Empty
          title="Ticket not available"
          hint="It may not exist, or it may belong to someone else. Access is enforced by the database."
        />
      </div>
    )
  }

  if (!ticket) return <div className="content"><ErrorNote error={error} /></div>

  return (
    <div className="content">
      <button className="sm" onClick={() => nav(-1)}>
        ← Back
      </button>

      <h1 style={{ marginTop: '0.75rem' }}>
        {ticket.is_emergency && <span className="badge danger">Emergency</span>} #
        {ticket.ticket_id} · {ticket.title}
      </h1>

      <div className="ticket-meta" style={{ marginBottom: '1rem' }}>
        <StatusBadge status={statusName} />
        <PriorityBadge
          priority={nameById(lookups.priorities, 'priority_id', ticket.priority_id)}
        />
        <span className="badge">
          {nameById(lookups.departments, 'department_id', ticket.department_id) ??
            'Awaiting routing'}
        </span>
        <span className="badge">
          {nameById(lookups.issueTypes, 'issue_type_id', ticket.issue_type_id) ??
            'Uncategorised'}
        </span>
        <span className="small muted">Opened {formatRelative(ticket.created_at)}</span>
      </div>

      <ErrorNote error={error} />

      {ticket.ai_routing_status === 'FAILED' && (
        <div className="alert warn">
          Automatic classification was unavailable, so this ticket was sent for manual
          review. Nothing was lost.
        </div>
      )}

      {reopenReq?.decision === 'PENDING' && (
        <div className="alert warn">
          <strong>Reopen requested</strong> {formatRelative(reopenReq.requested_at)}
          {reopenReq.reason && <> — “{reopenReq.reason}”</>}
          {isAdmin && (
            <div className="actions" style={{ marginTop: '0.6rem' }}>
              <button className="sm primary" disabled={busy} onClick={() => void decideReopen('APPROVED')}>
                Approve
              </button>
              <button className="sm" disabled={busy} onClick={() => void decideReopen('DENIED')}>
                Deny
              </button>
            </div>
          )}
        </div>
      )}

      <div className="card">
        <h3>Description</h3>
        <p style={{ whiteSpace: 'pre-wrap' }}>{ticket.description ?? ticket.original_text}</p>

        <div className="small muted">
          Location:{' '}
          {ticket.location_id ? <LocationLine locationId={ticket.location_id} /> : 'Not specified'}
        </div>

        {ticket.resolution_notes && (
          <>
            <h3 style={{ marginTop: '1rem' }}>Resolution</h3>
            <p style={{ whiteSpace: 'pre-wrap' }}>{ticket.resolution_notes}</p>
            <div className="small muted">Resolved {formatDateTime(ticket.resolved_at)}</div>
          </>
        )}
      </div>

      {attachments.length > 0 && (
        <div className="card">
          <h3>Photos</h3>
          <div className="attachments">
            {attachments.map((a) => (
              <a key={a.attachment_id} href={signedUrls[a.attachment_id]} target="_blank" rel="noreferrer">
                {signedUrls[a.attachment_id] ? (
                  <img src={signedUrls[a.attachment_id]} alt={a.file_name} />
                ) : (
                  <span className="small muted">{a.file_name}</span>
                )}
              </a>
            ))}
          </div>
          <div className="hint">Links expire after five minutes.</div>
        </div>
      )}

      {/* ---- actions ---- */}
      <div className="card">
        <h3>Actions</h3>
        <div className="actions">
          {isStaff && transitions.map((s) => (
            <button key={s} className="sm" disabled={busy} onClick={() => void changeStatus(s)}>
              Move to {humanize(s)}
            </button>
          ))}
          {isStaff && statusName !== STATUS.RESOLVED && (
            <button className="sm primary" disabled={busy} onClick={() => setShowResolve(true)}>
              Resolve
            </button>
          )}
          {isStaff && <button className="sm" disabled={busy} onClick={() => void claim()}>Claim</button>}
          {canReopen && (
            <button className="sm primary" disabled={busy} onClick={() => setShowReopen(true)}>
              Request reopen
            </button>
          )}
          {canWithdraw && (
            <button className="sm danger" disabled={busy} onClick={() => void withdraw()}>
              Withdraw ticket
            </button>
          )}
          {busy && <Spinner />}
        </div>
        {isOwner && statusName === STATUS.RESOLVED && !canReopen && reopenReq?.decision !== 'PENDING' && (
          <div className="hint">
            The {REOPEN_WINDOW_DAYS}-day reopen window has closed. Add a comment instead, or
            file a new ticket.
          </div>
        )}
      </div>

      {/* ---- comments ---- */}
      <div className="card">
        <h3>Comments</h3>
        {comments.length === 0 && <p className="muted small">No comments yet.</p>}
        {comments.map((c) => (
          <div key={c.comment_id} className={`comment ${c.is_internal ? 'internal' : ''}`}>
            <div className="head">
              {c.users ? `${c.users.first_name} ${c.users.last_name}` : 'Unknown'} ·{' '}
              {formatRelative(c.created_at)}
              {c.is_internal && <> · <strong>Internal note</strong></>}
            </div>
            <div style={{ whiteSpace: 'pre-wrap' }}>{c.message}</div>
          </div>
        ))}

        <div className="field" style={{ marginTop: '0.75rem' }}>
          <label htmlFor="comment">Add a comment</label>
          <textarea
            id="comment"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={
              statusName === STATUS.RESOLVED && isOwner
                ? 'A comment will not reopen this ticket. Use Request reopen for that.'
                : 'Add detail, ask a question, or share an update.'
            }
          />
        </div>
        <div className="actions">
          <button className="primary" disabled={busy || !draft.trim()} onClick={() => void addComment()}>
            Post
          </button>
          {isStaff && (
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', margin: 0, fontWeight: 400 }}>
              <input
                type="checkbox"
                checked={internal}
                onChange={(e) => setInternal(e.target.checked)}
                style={{ width: 'auto' }}
              />
              Internal note (hidden from requester)
            </label>
          )}
        </div>
      </div>

      {/* ---- history ---- */}
      <div className="card">
        <h3>History</h3>
        <ul className="timeline">
          {history.map((h) => (
            <li key={h.history_id}>
              <strong>{humanize(h.action)}</strong>
              {h.old_value && h.new_value && <> · {humanize(h.old_value)} → {humanize(h.new_value)}</>}
              {!h.old_value && h.new_value && <> · {h.new_value}</>}
              <div className="small muted">
                {h.users ? `${h.users.first_name} ${h.users.last_name}` : 'System'} ·{' '}
                {formatDateTime(h.created_at)}
              </div>
            </li>
          ))}
        </ul>
      </div>

      {showResolve && (
        <Modal title="Resolve ticket" onClose={() => setShowResolve(false)}>
          <div className="field">
            <label htmlFor="res">What was done?</label>
            <textarea
              id="res"
              value={resolution}
              onChange={(e) => setResolution(e.target.value)}
              placeholder="Replaced the thermostatic valve and confirmed the radiator cycles normally."
            />
            <div className="hint">The requester will see this.</div>
          </div>
          <div className="actions">
            <button className="primary" disabled={busy} onClick={() => void resolveTicket()}>
              Resolve
            </button>
            <button onClick={() => setShowResolve(false)}>Cancel</button>
          </div>
        </Modal>
      )}

      {showReopen && (
        <Modal title="Request reopen" onClose={() => setShowReopen(false)}>
          <p className="small muted">
            An administrator will review this. You have {REOPEN_WINDOW_DAYS} days from
            resolution to ask.
          </p>
          <div className="field">
            <label htmlFor="why">Why should this be reopened?</label>
            <textarea
              id="why"
              value={reopenReason}
              onChange={(e) => setReopenReason(e.target.value)}
              placeholder="The radiator started sticking again two days after the repair."
            />
          </div>
          <div className="actions">
            <button className="primary" disabled={busy} onClick={() => void requestReopen()}>
              Send request
            </button>
            <button onClick={() => setShowReopen(false)}>Cancel</button>
          </div>
        </Modal>
      )}
    </div>
  )
}

function LocationLine({ locationId }: { locationId: number }) {
  const [text, setText] = useState('…')
  useEffect(() => {
    supabase
      .from('locations')
      .select('room_number, exact_description, area_types(name), floors(floor_number, buildings(name))')
      .eq('location_id', locationId)
      .maybeSingle()
      .then(({ data }) => {
        if (!data) return setText('Not specified')
        const floor = data.floors as unknown as
          | { floor_number: string; buildings: { name: string } | null }
          | null
        const area = (data.area_types as unknown as { name: string } | null)?.name
        setText(
          [
            floor?.buildings?.name,
            floor && `Floor ${floor.floor_number}`,
            data.room_number && `Room ${data.room_number}`,
            area,
          ]
            .filter(Boolean)
            .join(' · ') || 'Not specified',
        )
      })
  }, [locationId])
  return <>{text}</>
}
