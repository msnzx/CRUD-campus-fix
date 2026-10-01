import { useState } from 'react'
import { supabase } from '../lib/supabase'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNote, Loading, Spinner } from '../components/ui'
import { useLookups } from '../lib/useLookups'
import { humanize } from '../lib/format'

// Users may change only first_name, last_name and phone: the column-level
// UPDATE grant on public.users stops anything else, role included.
export default function ProfilePage() {
  const { profile, refreshProfile } = useAuth()
  const { lookups } = useLookups()
  const [firstName, setFirstName] = useState(profile?.first_name ?? '')
  const [lastName, setLastName] = useState(profile?.last_name ?? '')
  const [phone, setPhone] = useState(profile?.phone ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)

  if (!profile) return <Loading />

  async function save(e: React.FormEvent) {
    e.preventDefault()
    if (!profile) return
    setBusy(true)
    setError(null)
    setSaved(false)
    const { error } = await supabase
      .from('users')
      .update({
        first_name: firstName.trim(),
        last_name: lastName.trim(),
        phone: phone.trim() || null,
      })
      .eq('user_id', profile.user_id)
    setBusy(false)
    if (error) {
      setError(error)
      return
    }
    setSaved(true)
    await refreshProfile()
  }

  const departments = profile.departmentIds
    .map((id) => lookups.departments.find((d) => d.department_id === id)?.name)
    .filter(Boolean)

  return (
    <div className="content narrow">
      <h1>Your profile</h1>

      <div className="card">
        <h3>Account</h3>
        <p className="small" style={{ margin: 0 }}>
          {profile.email} · <span className="badge">{humanize(profile.role)}</span>
          {departments.length > 0 && <> · {departments.join(', ')}</>}
        </p>
      </div>

      <form className="card" onSubmit={save}>
        <h3>Details</h3>
        <ErrorNote error={error} />
        {saved && <div className="alert ok">Saved.</div>}
        <div className="row">
          <div className="field">
            <label htmlFor="p-first">First name</label>
            <input id="p-first" value={firstName} onChange={(e) => setFirstName(e.target.value)} required />
          </div>
          <div className="field">
            <label htmlFor="p-last">Last name</label>
            <input id="p-last" value={lastName} onChange={(e) => setLastName(e.target.value)} required />
          </div>
        </div>
        <div className="field">
          <label htmlFor="p-phone">Phone (optional)</label>
          <input
            id="p-phone"
            type="tel"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            autoComplete="tel"
            placeholder="973-555-0142"
          />
          <div className="hint">Visible to campus staff, so they can reach you about a ticket.</div>
        </div>
        <button className="primary" type="submit" disabled={busy}>
          {busy ? <Spinner /> : 'Save changes'}
        </button>
      </form>
    </div>
  )
}
