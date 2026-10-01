import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { Empty, ErrorNote, Loading } from '../components/ui'
import { formatRelative } from '../lib/format'
import { notifyUnreadChanged } from '../components/NotificationBell'
import type { Tables } from '../lib/database.types'

// Rows are written by database triggers on ticket changes. RLS limits this
// query to the caller's own notifications.
export default function NotificationsPage() {
  const nav = useNavigate()
  const [rows, setRows] = useState<Tables<'notifications'>[] | null>(null)
  const [error, setError] = useState<unknown>(null)

  const load = useCallback(async () => {
    const { data, error } = await supabase
      .from('notifications')
      .select('*')
      .order('created_at', { ascending: false })
      .limit(100)
    if (error) setError(error)
    else setRows(data)
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function open(n: Tables<'notifications'>) {
    if (!n.read_at) {
      await supabase
        .from('notifications')
        .update({ read_at: new Date().toISOString() })
        .eq('notification_id', n.notification_id)
      notifyUnreadChanged()
    }
    if (n.ticket_id) nav(`/tickets/${n.ticket_id}`)
    else void load()
  }

  async function markAllRead() {
    const { error } = await supabase
      .from('notifications')
      .update({ read_at: new Date().toISOString() })
      .is('read_at', null)
    if (error) setError(error)
    notifyUnreadChanged()
    void load()
  }

  if (error) return <div className="content narrow"><ErrorNote error={error} /></div>
  if (!rows) return <Loading />

  const unread = rows.filter((r) => !r.read_at).length

  return (
    <div className="content narrow">
      <div className="page-head">
        <h1>Notifications</h1>
        {unread > 0 && (
          <button className="sm" onClick={() => void markAllRead()}>
            Mark all read
          </button>
        )}
      </div>

      {rows.length === 0 ? (
        <Empty
          title="You're all caught up"
          hint="Updates on your tickets and assignments will appear here."
        />
      ) : (
        rows.map((n) => (
          <button
            key={n.notification_id}
            className={`notification ${n.read_at ? '' : 'unread'}`}
            onClick={() => void open(n)}
          >
            <div>{n.message}</div>
            <div className="small muted">{formatRelative(n.created_at)}</div>
          </button>
        ))
      )}
    </div>
  )
}
