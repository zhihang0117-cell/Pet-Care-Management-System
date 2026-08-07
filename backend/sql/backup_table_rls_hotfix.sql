-- Targeted hotfix for the accidentally public backup table discovered on
-- 2026-08-07. Safe to run repeatedly; it does not change application/demo
-- table policies or any business data.
do $$
begin
  if to_regclass('public._status_backup_20260805') is not null then
    alter table public._status_backup_20260805 enable row level security;
    revoke all on table public._status_backup_20260805
      from public, anon, authenticated;
  end if;
end
$$;

notify pgrst, 'reload schema';
