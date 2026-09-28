-- Private bucket for ticket attachments. Files are never served from a
-- public URL; the client requests a short-lived signed URL for each read.

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'ticket-attachments',
  'ticket-attachments',
  false,
  10485760,  -- 10 MB
  array['image/jpeg','image/png','image/webp','image/gif','image/heic']
)
on conflict (id) do update
  set public             = excluded.public,
      file_size_limit    = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

-- Object paths are "<ticket_id>/<uuid>.<ext>". Access to an object is
-- therefore decided by access to its ticket, reusing the same helper the
-- table policies use. Guessing a path gains nothing.

create policy read_ticket_attachment_objects on storage.objects
  for select to authenticated
  using (
    bucket_id = 'ticket-attachments'
    and private.can_access_ticket((split_part(name, '/', 1))::bigint)
  );

create policy upload_ticket_attachment_objects on storage.objects
  for insert to authenticated
  with check (
    bucket_id = 'ticket-attachments'
    and owner_id = (select auth.uid())::text
    and private.can_access_ticket((split_part(name, '/', 1))::bigint)
  );

-- Staff may remove an attachment; requesters may not.
create policy staff_delete_attachment_objects on storage.objects
  for delete to authenticated
  using (
    bucket_id = 'ticket-attachments'
    and private.is_staff()
    and private.can_access_ticket((split_part(name, '/', 1))::bigint)
  );
