-- Link public.users to Supabase Auth and provision profiles on signup.

-- 1. Referential integrity between the auth system and the profile table.
alter table public.users
  add constraint users_user_id_fkey
  foreign key (user_id) references auth.users(id) on delete cascade;

-- 2. Provision a profile row whenever a new auth user is created.
--    Role is ALWAYS hard-coded to STUDENT. It is never read from
--    raw_user_meta_data, which is user-editable and therefore unsafe
--    as an authorization input. Elevation is a System Admin action.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_role_id integer;
begin
  select role_id into v_role_id
  from public.roles
  where name = 'STUDENT';

  insert into public.users (user_id, first_name, last_name, email, role_id)
  values (
    new.id,
    coalesce(nullif(trim(new.raw_user_meta_data ->> 'first_name'), ''), 'Unknown'),
    coalesce(nullif(trim(new.raw_user_meta_data ->> 'last_name'), ''), 'Unknown'),
    new.email,
    v_role_id
  );

  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row
  execute function public.handle_new_user();

-- 3. Keep the profile email in step with the auth email.
create or replace function public.handle_user_email_change()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.email is distinct from old.email then
    update public.users
       set email = new.email,
           updated_at = now()
     where user_id = new.id;
  end if;
  return new;
end;
$$;

create trigger on_auth_user_email_changed
  after update of email on auth.users
  for each row
  execute function public.handle_user_email_change();
