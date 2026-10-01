import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { STATUS } from '../lib/domain'
import { humanize } from '../lib/format'

// The CU cougar mark. The artwork has a white background, so it always sits
// on a white disc, including in dark mode.
export function BrandMark({ size = 32 }: { size?: number }) {
  return (
    <img
      src="/caldwell-logo.png"
      alt=""
      width={size}
      height={size}
      className="brand-mark"
    />
  )
}

export function Spinner() {
  return <span className="spinner" role="status" aria-label="Loading" />
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="empty">
      <Spinner />
      <div style={{ marginTop: '0.6rem' }}>{label}</div>
    </div>
  )
}

export function Empty({ title, hint }: { title: string; hint?: ReactNode }) {
  return (
    <div className="empty">
      <div style={{ fontWeight: 600, marginBottom: '0.3rem' }}>{title}</div>
      {hint && <div className="small">{hint}</div>}
    </div>
  )
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null
  const message = error instanceof Error ? error.message : String(error)
  return (
    <div className="alert error" role="alert">
      {message}
    </div>
  )
}

const STATUS_TONE: Record<string, string> = {
  [STATUS.NEW]: 'accent',
  [STATUS.AI_PROCESSING]: '',
  [STATUS.NEEDS_REVIEW]: 'warn',
  [STATUS.ASSIGNED]: '',
  [STATUS.IN_PROGRESS]: 'accent',
  [STATUS.WAITING_FOR_USER]: 'warn',
  [STATUS.RESOLVED]: 'ok',
  [STATUS.CLOSED]: '',
  [STATUS.REOPENED]: 'warn',
}

export function StatusBadge({ status }: { status: string | null }) {
  if (!status) return <span className="badge">Unknown</span>
  return <span className={`badge ${STATUS_TONE[status] ?? ''}`}>{humanize(status)}</span>
}

const PRIORITY_TONE: Record<string, string> = {
  LOW: '',
  MEDIUM: '',
  HIGH: 'warn',
  URGENT: 'danger',
}

export function PriorityBadge({ priority }: { priority: string | null }) {
  if (!priority) return null
  return <span className={`badge ${PRIORITY_TONE[priority] ?? ''}`}>{humanize(priority)}</span>
}

export function Modal({
  title,
  children,
  onClose,
}: {
  title: string
  children: ReactNode
  onClose: () => void
}) {
  const dialogRef = useRef<HTMLDivElement>(null)
  // Callers pass a fresh closure each render; keep the latest without
  // re-running the mount effect, which would steal focus on every keystroke.
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  // Escape closes; focus moves into the dialog on open and back to whatever
  // opened it on close, so keyboard users are not stranded.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    const first = dialogRef.current?.querySelector<HTMLElement>(
      'input, textarea, select, button, [href]',
    )
    first?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCloseRef.current()
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      opener?.focus()
    }
  }, [])

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={onClose}
    >
      <div className="modal" ref={dialogRef} onClick={(e) => e.stopPropagation()}>
        <h2>{title}</h2>
        {children}
      </div>
    </div>
  )
}
