import { useState } from 'react'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNote, Spinner } from '../components/ui'

export default function LoginPage() {
  const { signIn, signUp } = useAuth()
  const [mode, setMode] = useState<'signin' | 'signup'>('signin')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setNotice(null)
    setBusy(true)
    try {
      if (mode === 'signin') {
        await signIn(email.trim(), password)
      } else {
        const { needsConfirmation } = await signUp({
          email: email.trim(),
          password,
          firstName: firstName.trim(),
          lastName: lastName.trim(),
        })
        if (needsConfirmation) {
          setNotice('Account created. Check your email to confirm before signing in.')
          setMode('signin')
        }
      }
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="content narrow" style={{ paddingTop: '3rem' }}>
      <h1>CampusFix</h1>
      <p className="muted">Report and track campus issues.</p>

      <div className="card">
        <div className="actions" style={{ marginBottom: '1rem' }}>
          <button
            className={mode === 'signin' ? 'primary sm' : 'sm'}
            onClick={() => {
              setMode('signin')
              setError(null)
            }}
            type="button"
          >
            Sign in
          </button>
          <button
            className={mode === 'signup' ? 'primary sm' : 'sm'}
            onClick={() => {
              setMode('signup')
              setError(null)
            }}
            type="button"
          >
            Create account
          </button>
        </div>

        <ErrorNote error={error} />
        {notice && <div className="alert ok">{notice}</div>}

        <form onSubmit={onSubmit}>
          {mode === 'signup' && (
            <div className="row">
              <div className="field">
                <label htmlFor="first">First name</label>
                <input
                  id="first"
                  value={firstName}
                  onChange={(e) => setFirstName(e.target.value)}
                  required
                  autoComplete="given-name"
                />
              </div>
              <div className="field">
                <label htmlFor="last">Last name</label>
                <input
                  id="last"
                  value={lastName}
                  onChange={(e) => setLastName(e.target.value)}
                  required
                  autoComplete="family-name"
                />
              </div>
            </div>
          )}

          <div className="field">
            <label htmlFor="email">Caldwell email</label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="email"
              placeholder="you@caldwell.edu"
            />
            <div className="hint">Accounts must use a @caldwell.edu address.</div>
          </div>

          <div className="field">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
            />
          </div>

          <button className="primary" type="submit" disabled={busy}>
            {busy ? <Spinner /> : mode === 'signin' ? 'Sign in' : 'Create account'}
          </button>
        </form>
      </div>

      <p className="small muted">
        New accounts are created as students. Staff and administrator access is granted by a
        system administrator.
      </p>
    </div>
  )
}
