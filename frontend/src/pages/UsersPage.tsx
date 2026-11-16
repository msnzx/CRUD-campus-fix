import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { supabase } from '../lib/supabase'
import { useAuth } from '../auth/AuthProvider'
import { activeDepartments, useLookups } from '../lib/useLookups'
import { Empty, ErrorNote, Loading, Modal } from '../components/ui'
import { humanize } from '../lib/format'
import { ROLE } from '../lib/domain'
import type { RoleName } from '../lib/domain'
import { inviteUser, sendPasswordReset } from '../lib/adminApi'

interface UserRow {
  user_id: string
  first_name: string
  last_name: string
  email: string
  active: boolean
  roles: { name: RoleName } | null
  user_departments: { department_id: number }[]
}

const ROLE_OPTIONS: RoleName[] = [
  ROLE.STUDENT,
  ROLE.DEPARTMENT_STAFF,
  ROLE.DEPARTMENT_ADMIN,
  ROLE.SYSTEM_ADMIN,
]

// System admins manage every account: role, active flag, departments.
// Department admins manage membership of their own departments only.
// Both are enforced inside the RPCs; this page just hides what would fail.
export default function UsersPage() {
  const { profile, isSystemAdmin } = useAuth()
  const { lookups, ready } = useLookups()
  const [users, setUsers] = useState<UserRow[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [inviteOpen, setInviteOpen] = useState(false)
  const [inviteBusy, setInviteBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [invite, setInvite] = useState({ firstName: '', lastName: '', email: '' })

  const load = useCallback(async () => {
    const { data, error } = await supabase
      .from('users')
      .select('user_id, first_name, last_name, email, active, roles(name), user_departments(department_id)')
      .order('last_name')
    if (error) setError(error)
    else setUsers(data as unknown as UserRow[])
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // Departments this admin may add people to.
  const manageable = useMemo(() => {
    const all = activeDepartments(lookups)
    if (isSystemAdmin) return all
    const mine = new Set(profile?.departmentIds ?? [])
    return all.filter((d) => mine.has(d.department_id))
  }, [lookups, isSystemAdmin, profile])

  async function call(userId: string, fn: () => PromiseLike<{ error: unknown }>) {
    setBusyId(userId)
    setError(null)
    const { error } = await fn()
    if (error) setError(error)
    await load()
    setBusyId(null)
  }

  async function createInvitation(event: FormEvent) {
    event.preventDefault()
    setInviteBusy(true)
    setError(null)
    setNotice(null)
    try {
      const message = await inviteUser(invite)
      setNotice(message)
      setInviteOpen(false)
      setInvite({ firstName: '', lastName: '', email: '' })
      await load()
    } catch (err) {
      setError(err)
    } finally {
      setInviteBusy(false)
    }
  }

  async function resetPassword(userId: string, email: string) {
    setBusyId(userId)
    setError(null)
    setNotice(null)
    try {
      setNotice(await sendPasswordReset(email))
    } catch (err) {
      setError(err)
    } finally {
      setBusyId(null)
    }
  }

  if (!users || !ready) return error ? <div className="content"><ErrorNote error={error} /></div> : <Loading />

  const q = query.trim().toLowerCase()
  const visible = users.filter((u) => {
    // Department admins only manage staff; students are not theirs to see here.
    if (!isSystemAdmin && u.roles?.name === ROLE.STUDENT) return false
    if (!q) return true
    return `${u.first_name} ${u.last_name} ${u.email}`.toLowerCase().includes(q)
  })

  return (
    <div className="content admin-page">
      <section className="operations-hero admin-hero">
        <div><p className="eyebrow">Administration</p><h1>{isSystemAdmin ? 'People & access' : 'Department staff'}</h1>
          <p>
        {isSystemAdmin
          ? 'Change roles, deactivate accounts that have left, and set department membership. New sign-ups always start as students.'
          : 'Add or remove staff in your departments. Ask a system admin to change someone’s role.'}
          </p></div>
        <div className="actions">
          {isSystemAdmin && <button className="primary" onClick={() => setInviteOpen(true)}>+ Add user</button>}
          <div className="queue-count"><strong>{visible.length}</strong><span>people shown</span></div>
        </div>
      </section>

      <ErrorNote error={error} />
      {notice && <div className="alert ok" role="status">{notice}</div>}

      <div className="card card-tight queue-controls">
        <div className="section-label"><strong>Directory</strong><span>Search people and manage their access</span></div>
        <label htmlFor="u-search">Search</label>
        <input
          id="u-search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Name or email"
        />
      </div>

      {visible.length === 0 && <Empty title="No matching accounts" />}

      {visible.map((u) => {
        const role = u.roles?.name ?? ROLE.STUDENT
        const memberOf = new Set(u.user_departments.map((d) => d.department_id))
        const busy = busyId === u.user_id
        const isSelf = u.user_id === profile?.user_id
        const addable = manageable.filter((d) => !memberOf.has(d.department_id))

        return (
          <div key={u.user_id} className={`card card-tight ${u.active ? '' : 'inactive'}`}>
            <div className="page-head" style={{ marginBottom: '0.4rem' }}>
              <div>
                <strong>
                  {u.first_name} {u.last_name}
                </strong>{' '}
                {!u.active && <span className="badge danger">Deactivated</span>}
                <div className="small muted">{u.email}</div>
              </div>
              {isSystemAdmin ? (
                <div className="actions">
                  <select
                    aria-label={`Role for ${u.email}`}
                    value={role}
                    disabled={busy || isSelf}
                    onChange={(e) =>
                      void call(u.user_id, () =>
                        supabase.rpc('admin_set_user_role', { p_user_id: u.user_id, p_role: e.target.value }),
                      )
                    }
                    style={{ width: 'auto' }}
                  >
                    {ROLE_OPTIONS.map((r) => (
                      <option key={r} value={r}>
                        {humanize(r)}
                      </option>
                    ))}
                  </select>
                  {!isSelf && (
                    <>
                      <button className="sm" disabled={busy || !u.active} onClick={() => void resetPassword(u.user_id, u.email)}>
                        Reset password
                      </button>
                      <button
                        className={`sm ${u.active ? 'danger' : ''}`}
                        disabled={busy}
                        onClick={() => {
                          if (u.active && !window.confirm(`Remove ${u.first_name} ${u.last_name}'s access? Their tickets will be kept.`)) return
                          void call(u.user_id, () =>
                            supabase.rpc('admin_set_user_active', { p_user_id: u.user_id, p_active: !u.active }),
                          )
                        }}
                      >
                        {u.active ? 'Remove access' : 'Reactivate'}
                      </button>
                    </>
                  )}
                </div>
              ) : (
                <span className="badge">{humanize(role)}</span>
              )}
            </div>

            {role !== ROLE.STUDENT && (
              <div className="ticket-meta">
                {[...memberOf].map((id) => {
                  const name = lookups.departments.find((d) => d.department_id === id)?.name ?? `#${id}`
                  const canRemove = manageable.some((d) => d.department_id === id)
                  return (
                    <span key={id} className="badge accent">
                      {name}
                      {canRemove && (
                        <button
                          className="chip-x"
                          aria-label={`Remove ${u.email} from ${name}`}
                          disabled={busy}
                          onClick={() =>
                            void call(u.user_id, () =>
                              supabase.rpc('set_department_member', {
                                p_user_id: u.user_id,
                                p_department_id: id,
                                p_member: false,
                              }),
                            )
                          }
                        >
                          ×
                        </button>
                      )}
                    </span>
                  )
                })}
                {addable.length > 0 && (
                  <select
                    aria-label={`Add ${u.email} to a department`}
                    value=""
                    disabled={busy}
                    onChange={(e) =>
                      e.target.value &&
                      void call(u.user_id, () =>
                        supabase.rpc('set_department_member', {
                          p_user_id: u.user_id,
                          p_department_id: Number(e.target.value),
                          p_member: true,
                        }),
                      )
                    }
                    style={{ width: 'auto', padding: '0.2rem 0.5rem', fontSize: '0.82rem' }}
                  >
                    <option value="">+ Add to department</option>
                    {addable.map((d) => (
                      <option key={d.department_id} value={d.department_id}>
                        {d.name}
                      </option>
                    ))}
                  </select>
                )}
                {memberOf.size === 0 && addable.length === 0 && (
                  <span className="small muted">No department</span>
                )}
              </div>
            )}
          </div>
        )
      })}

      {inviteOpen && (
        <Modal title="Add a CaldwellFix user" onClose={() => !inviteBusy && setInviteOpen(false)}>
          <form onSubmit={(event) => void createInvitation(event)}>
            <p className="muted">They will receive an email invitation to set a password. New accounts begin as students; you can then change their role above.</p>
            <div className="form-grid">
              <label>First name<input required value={invite.firstName} onChange={(e) => setInvite({ ...invite, firstName: e.target.value })} /></label>
              <label>Last name<input required value={invite.lastName} onChange={(e) => setInvite({ ...invite, lastName: e.target.value })} /></label>
            </div>
            <label>Email<input required type="email" value={invite.email} onChange={(e) => setInvite({ ...invite, email: e.target.value })} placeholder="name@caldwell.edu" /></label>
            <div className="actions" style={{ marginTop: '1rem', justifyContent: 'flex-end' }}>
              <button type="button" className="secondary" disabled={inviteBusy} onClick={() => setInviteOpen(false)}>Cancel</button>
              <button className="primary" disabled={inviteBusy}>{inviteBusy ? 'Sending…' : 'Send invitation'}</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  )
}
