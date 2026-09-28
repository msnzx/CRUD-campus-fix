import { supabase } from './supabase'

const API_URL = import.meta.env.VITE_API_URL

/**
 * Ask the backend to classify a ticket.
 *
 * Every failure mode here is non-fatal by design. The ticket is already
 * committed before this is called; if the classifier is down, misconfigured,
 * or simply absent, the ticket keeps the deterministic department derived
 * from its issue type and a human picks up the rest.
 *
 * Callers should treat this as fire-and-forget.
 */
export async function classifyTicket(ticketId: number): Promise<void> {
  if (!API_URL) return // No backend configured — deterministic routing stands.

  const { data } = await supabase.auth.getSession()
  const token = data.session?.access_token
  if (!token) return

  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 8000)

  try {
    await fetch(`${API_URL}/api/tickets/${ticketId}/classify`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      signal: controller.signal,
    })
  } finally {
    clearTimeout(timeout)
  }
}
