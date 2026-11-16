-- Allow the existing System Admin building-update policy to save GPS centroids.
--
-- This is deliberately a column-level grant only. The pre-existing
-- admin_update_building_map RLS policy still requires private.is_system_admin(),
-- and no other building fields, ticket records, or map-pin coordinates are
-- changed by this migration.

grant update (latitude, longitude) on public.buildings to authenticated;
