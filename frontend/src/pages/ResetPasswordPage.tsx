import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { BrandMark, ErrorNote, Spinner } from '../components/ui'

// Landing page for the emailed reset link. supabase-js reads the recovery
// token from the URL hash and signs the user in, so by the time this renders
// a valid link has produced a session. No session means the link was bad,
// already used, or expired.
export default function ResetPasswordPage() {
  const { session, updatePassword } = useAuth()
  const navigate = useNavigate()
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    if (password !== confirm) {
      setError(new Error('The two passwords do not match.'))
      return
    }
    setBusy(true)
    try {
      await updatePassword(password)
      setDone(true)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-logo">
          <BrandMark size={64} />
        </div>
        <h1>Choose a new password</h1>

        {!session ? (
          <>
            <p className="auth-sub">
              This reset link is invalid or has expired. Links work once and only for a short time.
            </p>
            <button className="primary block" type="button" onClick={() => navigate('/login')}>
              Request a new link →
            </button>
          </>
        ) : done ? (
          <>
            <div className="alert ok">Your password has been updated.</div>
            <button className="primary block" type="button" onClick={() => navigate('/tickets')}>
              Continue to CampusFix →
            </button>
          </>
        ) : (
          <>
            <p className="auth-sub">Signed in as {session.user.email}</p>
            <ErrorNote error={error} />
            <form onSubmit={onSubmit}>
              <div className="field">
                <label htmlFor="new-password">New password</label>
                <input
                  id="new-password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  minLength={8}
                  autoComplete="new-password"
                />
                <div className="hint">At least 8 characters.</div>
              </div>
              <div className="field">
                <label htmlFor="confirm-password">Confirm new password</label>
                <input
                  id="confirm-password"
                  type="password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  required
                  minLength={8}
                  autoComplete="new-password"
                />
              </div>
              <button className="primary block" type="submit" disabled={busy}>
                {busy ? <Spinner /> : 'Update password →'}
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  )
}
