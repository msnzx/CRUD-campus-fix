import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { Session } from '@supabase/supabase-js'
import { supabase } from '../lib/supabase'
import { ADMIN_ROLES, ROLE, STAFF_ROLES } from '../lib/domain'
import type { RoleName } from '../lib/domain'

export interface Profile {
  user_id: string
  first_name: string
  last_name: string
  email: string
  phone: string | null
  active: boolean
  role: RoleName
  departmentIds: number[]
}

interface AuthValue {
  session: Session | null
  profile: Profile | null
  loading: boolean
  isStaff: boolean
  isAdmin: boolean
  isSystemAdmin: boolean
  signIn(email: string, password: string): Promise<void>
  signUp(args: {
    email: string
    password: string
    firstName: string
    lastName: string
  }): Promise<{ needsConfirmation: boolean }>
  signOut(): Promise<void>
  refreshProfile(): Promise<void>
}

const AuthContext = createContext<AuthValue | null>(null)

async function loadProfile(userId: string): Promise<Profile | null> {
  // RLS restricts this to the caller's own row, so no filter is strictly
  // required — but being explicit keeps the intent readable.
  const { data, error } = await supabase
    .from('users')
    .select('user_id, first_name, last_name, email, phone, active, roles(name)')
    .eq('user_id', userId)
    .maybeSingle()

  if (error || !data) return null

  const { data: depts } = await supabase
    .from('user_departments')
    .select('department_id')
    .eq('user_id', userId)

  const roleName = (data.roles as unknown as { name: string } | null)?.name
  return {
    user_id: data.user_id,
    first_name: data.first_name,
    last_name: data.last_name,
    email: data.email,
    phone: data.phone,
    active: data.active,
    role: (roleName ?? ROLE.STUDENT) as RoleName,
    departmentIds: (depts ?? []).map((d) => d.department_id),
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [profile, setProfile] = useState<Profile | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true

    supabase.auth.getSession().then(async ({ data }) => {
      if (!active) return
      setSession(data.session)
      if (data.session) setProfile(await loadProfile(data.session.user.id))
      setLoading(false)
    })

    const { data: sub } = supabase.auth.onAuthStateChange(async (_event, next) => {
      if (!active) return
      setSession(next)
      setProfile(next ? await loadProfile(next.user.id) : null)
      setLoading(false)
    })

    return () => {
      active = false
      sub.subscription.unsubscribe()
    }
  }, [])

  const value = useMemo<AuthValue>(() => {
    const role = profile?.role
    return {
      session,
      profile,
      loading,
      isStaff: !!role && STAFF_ROLES.includes(role),
      isAdmin: !!role && ADMIN_ROLES.includes(role),
      isSystemAdmin: role === ROLE.SYSTEM_ADMIN,

      async signIn(email, password) {
        const { error } = await supabase.auth.signInWithPassword({ email, password })
        if (error) throw error
      },

      async signUp({ email, password, firstName, lastName }) {
        const { data, error } = await supabase.auth.signUp({
          email,
          password,
          options: { data: { first_name: firstName, last_name: lastName } },
        })
        if (error) {
          // The profile trigger enforces the @caldwell.edu CHECK constraint.
          // When it trips, Postgres surfaces an opaque database error, so
          // translate it into something a person can act on.
          if (/database error|constraint|check/i.test(error.message)) {
            throw new Error(
              'Sign-up failed. CampusFix accounts must use a @caldwell.edu email address.',
            )
          }
          throw error
        }
        return { needsConfirmation: !data.session }
      },

      async signOut() {
        await supabase.auth.signOut()
        setProfile(null)
      },

      async refreshProfile() {
        if (session?.user.id) setProfile(await loadProfile(session.user.id))
      },
    }
  }, [session, profile, loading])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
