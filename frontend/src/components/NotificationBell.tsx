import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { supabase } from '../lib/supabase'

const UNREAD_EVENT = 'campusfix:unread-changed'

// Called after marking notifications read so the badge updates immediately
// instead of on the next poll.
export function notifyUnreadChanged() {
  window.dispatchEvent(new Event(UNREAD_EVENT))
}

const POLL_MS = 60_000

export default function NotificationBell() {
  const [count, setCount] = useState(0)
  const { pathname } = useLocation()

  useEffect(() => {
    let active = true
    const refresh = () =>
      supabase
        .from('notifications')
        .select('notification_id', { count: 'exact', head: true })
        .is('read_at', null)
        .then(({ count }) => {
          if (active) setCount(count ?? 0)
        })

    void refresh()
    const timer = window.setInterval(refresh, POLL_MS)
    window.addEventListener(UNREAD_EVENT, refresh)
    return () => {
      active = false
      window.clearInterval(timer)
      window.removeEventListener(UNREAD_EVENT, refresh)
    }
    // Navigating is a cheap moment to re-check: actions that notify others
    // often happen just before it.
  }, [pathname])

  const label = count > 0 ? `Notifications, ${count} unread` : 'Notifications'
  return (
    <Link to="/notifications" className="bell" aria-label={label} title={label}>
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path
          d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.94 1.94 0 0 0 3.4 0"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      {count > 0 && <span className="bell-count">{count > 99 ? '99+' : count}</span>}
    </Link>
  )
}
