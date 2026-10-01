import { supabase, ATTACHMENT_BUCKET } from './supabase'
import { ACCEPTED_IMAGE_TYPES, MAX_ATTACHMENT_BYTES } from './domain'
import { formatBytes } from './format'

// Checked before upload for a friendly message. The bucket enforces the same
// type and size limits server-side, so this is convenience, not security.
export function validateImage(f: File): string | null {
  if (!ACCEPTED_IMAGE_TYPES.includes(f.type)) {
    return `${f.name} is not a supported image type. Use JPEG, PNG, WebP, GIF, or HEIC.`
  }
  if (f.size > MAX_ATTACHMENT_BYTES) {
    return `${f.name} is ${formatBytes(f.size)}. The limit is ${formatBytes(MAX_ATTACHMENT_BYTES)}.`
  }
  return null
}

// Object paths are "<ticket_id>/<uuid>.<ext>": the storage policies decide
// access from the leading ticket id.
export async function uploadAttachment(
  ticketId: number,
  file: File,
  userId: string,
): Promise<Error | null> {
  const ext = file.name.split('.').pop() ?? 'jpg'
  const path = `${ticketId}/${crypto.randomUUID()}.${ext}`
  const { error: upErr } = await supabase.storage
    .from(ATTACHMENT_BUCKET)
    .upload(path, file, { contentType: file.type, upsert: false })
  if (upErr) return upErr

  const { error } = await supabase.from('attachments').insert({
    ticket_id: ticketId,
    uploaded_by: userId,
    file_name: file.name,
    file_url: path,
    file_type: file.type,
    file_size_bytes: file.size,
  })
  return error ? new Error(error.message) : null
}
