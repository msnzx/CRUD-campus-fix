-- Trigger functions are invoked by the trigger mechanism, which does not
-- consult EXECUTE privilege. Postgres nonetheless grants EXECUTE to PUBLIC
-- on every new function, exposing each one at /rest/v1/rpc/<name>.

revoke execute on function public.handle_new_user()          from public, anon, authenticated;
revoke execute on function public.handle_user_email_change() from public, anon, authenticated;
revoke execute on function public.normalize_new_ticket()     from public, anon, authenticated;
revoke execute on function public.set_updated_at()           from public, anon, authenticated;

-- Same exposure applies to the RLS helpers. They live in `private`, which is
-- not an exposed schema, but PUBLIC still holds EXECUTE by default.
revoke execute on all functions in schema private from public, anon;
