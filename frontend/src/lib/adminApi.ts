import { supabase } from './supabase'

const API_URL = import.meta.env.VITE_API_URL

type InvitePayload = { firstName: string; lastName: string; email: string }

async function callAdmin(path: string, body: Record<string, string>) {
  if (!API_URL) throw new Error('Account help is not configured. Start the CampusFix backend to use this action.')
  const { data } = await supabase.auth.getSession()
  const token = data.session?.access_token
  if (!token) throw new Error('Your session has ended. Please sign in again.')

  const response = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  })
  const responseData = await response.json().catch(() => ({})) as { detail?: string; message?: string }
  if (!response.ok) throw new Error(responseData.detail ?? 'The account action could not be completed.')
  return responseData.message ?? 'Done.'
}

export function inviteUser(payload: InvitePayload) { return callAdmin('/api/admin/users/invite', payload) }
export function sendPasswordReset(email: string) { return callAdmin('/api/admin/users/password-reset', { email }) }
