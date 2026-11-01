-- D2WD: private Manifest sync audit, evidence, and review queue.
-- The catalogue tables remain in public; this schema contains maintainer-only operational data.

create schema if not exists d2wd_internal;
revoke all on schema d2wd_internal from public, anon, authenticated;
grant usage on schema d2wd_internal to service_role;

create table if not exists d2wd_internal.manifest_sync_runs (
  id uuid primary key default gen_random_uuid(),
  source text not null default 'bungie_manifest',
  manifest_version text not null,
  manifest_endpoint text not null default 'https://www.bungie.net/Platform/Destiny2/Manifest/',
  generator_version text not null,
  run_mode text not null default 'manual',
  status text not null default 'running'
    check (status in ('running','succeeded','review_required','partial','failed')),
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  stats jsonb not null default '{}'::jsonb,
  warnings jsonb not null default '[]'::jsonb,
  error_text text,
  constraint manifest_sync_runs_completion_check
    check (completed_at is null or completed_at >= started_at)
);

create index if not exists manifest_sync_runs_version_idx
  on d2wd_internal.manifest_sync_runs (manifest_version, started_at desc);
create index if not exists manifest_sync_runs_status_idx
  on d2wd_internal.manifest_sync_runs (status, started_at desc);

create table if not exists d2wd_internal.catalogue_field_evidence (
  id uuid primary key default gen_random_uuid(),
  run_id uuid references d2wd_internal.manifest_sync_runs(id) on delete set null,
  entity_type text not null check (entity_type in ('weapon','perk','pool')),
  entity_id text not null,
  field_name text not null,
  old_value jsonb,
  new_value jsonb,
  source_type text not null check (source_type in (
    'bungie_manifest',
    'historical_manifest',
    'light_gg',
    'manual_review',
    'same_hash_catalogue_match',
    'derived'
  )),
  source_ref text,
  source_version text,
  confidence text not null check (confidence in ('exact','corroborated','candidate')),
  review_status text not null default 'pending'
    check (review_status in ('accepted','pending','rejected')),
  recorded_at timestamptz not null default now(),
  notes text
);

create index if not exists catalogue_field_evidence_entity_idx
  on d2wd_internal.catalogue_field_evidence (entity_type, entity_id, field_name);
create index if not exists catalogue_field_evidence_run_idx
  on d2wd_internal.catalogue_field_evidence (run_id, recorded_at desc);

create table if not exists d2wd_internal.catalogue_review_queue (
  id uuid primary key default gen_random_uuid(),
  entity_type text not null check (entity_type in ('weapon','perk','pool')),
  entity_id text not null,
  issue_type text not null,
  priority text not null default 'medium'
    check (priority in ('low','medium','high','critical')),
  status text not null default 'open'
    check (status in ('open','in_progress','resolved','wont_fix')),
  details jsonb not null default '{}'::jsonb,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  resolved_at timestamptz,
  unique (entity_type, entity_id, issue_type),
  constraint catalogue_review_queue_resolution_check
    check (
      (status in ('resolved','wont_fix') and resolved_at is not null)
      or (status in ('open','in_progress') and resolved_at is null)
    )
);

create index if not exists catalogue_review_queue_status_idx
  on d2wd_internal.catalogue_review_queue (status, priority, last_seen_at desc);
create index if not exists catalogue_review_queue_entity_idx
  on d2wd_internal.catalogue_review_queue (entity_type, entity_id);

alter table d2wd_internal.manifest_sync_runs enable row level security;
alter table d2wd_internal.catalogue_field_evidence enable row level security;
alter table d2wd_internal.catalogue_review_queue enable row level security;

revoke all on all tables in schema d2wd_internal from public, anon, authenticated;
grant select, insert, update, delete on all tables in schema d2wd_internal to service_role;
alter default privileges in schema d2wd_internal
  revoke all on tables from public, anon, authenticated;
alter default privileges in schema d2wd_internal
  grant select, insert, update, delete on tables to service_role;

drop policy if exists manifest_sync_runs_service_role on d2wd_internal.manifest_sync_runs;
create policy manifest_sync_runs_service_role
  on d2wd_internal.manifest_sync_runs for all to service_role
  using (true) with check (true);

drop policy if exists catalogue_field_evidence_service_role on d2wd_internal.catalogue_field_evidence;
create policy catalogue_field_evidence_service_role
  on d2wd_internal.catalogue_field_evidence for all to service_role
  using (true) with check (true);

drop policy if exists catalogue_review_queue_service_role on d2wd_internal.catalogue_review_queue;
create policy catalogue_review_queue_service_role
  on d2wd_internal.catalogue_review_queue for all to service_role
  using (true) with check (true);

create or replace function public.d2wd_start_manifest_sync(
  p_manifest_version text,
  p_generator_version text,
  p_run_mode text default 'manual',
  p_warnings jsonb default '[]'::jsonb
)
returns uuid
language plpgsql
security definer
set search_path = pg_catalog, d2wd_internal, pg_temp
as $$
declare
  v_run_id uuid;
begin
  if p_manifest_version is null or length(btrim(p_manifest_version)) = 0 then
    raise exception 'manifest_version is required';
  end if;
  if p_generator_version is null or length(btrim(p_generator_version)) = 0 then
    raise exception 'generator_version is required';
  end if;
  if coalesce(p_run_mode,'') not in ('github_actions','manual','manual_reconciliation') then
    raise exception 'unsupported run_mode: %', p_run_mode;
  end if;
  if jsonb_typeof(coalesce(p_warnings,'[]'::jsonb)) <> 'array' then
    raise exception 'warnings must be a JSON array';
  end if;

  insert into d2wd_internal.manifest_sync_runs (
    manifest_version,generator_version,run_mode,status,warnings
  )
  values (
    p_manifest_version,p_generator_version,p_run_mode,'running',coalesce(p_warnings,'[]'::jsonb)
  )
  returning id into v_run_id;

  return v_run_id;
end;
$$;

create or replace function public.d2wd_finish_manifest_sync(
  p_run_id uuid,
  p_status text,
  p_stats jsonb default '{}'::jsonb,
  p_warnings jsonb default '[]'::jsonb,
  p_error_text text default null
)
returns boolean
language plpgsql
security definer
set search_path = pg_catalog, d2wd_internal, pg_temp
as $$
begin
  if p_run_id is null then raise exception 'run_id is required'; end if;
  if coalesce(p_status,'') not in ('succeeded','review_required','partial','failed') then
    raise exception 'unsupported terminal status: %', p_status;
  end if;
  if jsonb_typeof(coalesce(p_stats,'{}'::jsonb)) <> 'object' then
    raise exception 'stats must be a JSON object';
  end if;
  if jsonb_typeof(coalesce(p_warnings,'[]'::jsonb)) <> 'array' then
    raise exception 'warnings must be a JSON array';
  end if;

  update d2wd_internal.manifest_sync_runs
  set status=p_status,completed_at=now(),stats=coalesce(p_stats,'{}'::jsonb),
      warnings=coalesce(p_warnings,'[]'::jsonb),error_text=p_error_text
  where id=p_run_id and status='running' and completed_at is null;

  if not found then
    raise exception 'manifest sync run % does not exist or is already finished',p_run_id;
  end if;
  return true;
end;
$$;

create or replace function public.d2wd_get_manifest_sync_state()
returns jsonb
language sql
security definer
set search_path = pg_catalog, d2wd_internal, pg_temp
as $$
  select jsonb_build_object(
    'previousManifestFingerprint',stats->>'manifest_fingerprint',
    'manifestVersion',manifest_version,
    'completedAt',completed_at
  )
  from d2wd_internal.manifest_sync_runs
  where status in ('succeeded','review_required')
    and completed_at is not null
    and stats ? 'manifest_fingerprint'
  order by completed_at desc
  limit 1;
$$;

create or replace function public.d2wd_record_sync_evidence(
  p_run_id uuid,
  p_entries jsonb
)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, d2wd_internal, pg_temp
as $$
declare
  v_entry jsonb;
  v_count integer;
begin
  if p_run_id is null then raise exception 'run_id is required'; end if;
  if jsonb_typeof(p_entries) is distinct from 'array' then
    raise exception 'entries must be a JSON array';
  end if;
  if jsonb_array_length(p_entries)>250 then
    raise exception 'at most 250 evidence records are allowed per call';
  end if;
  if not exists (
    select 1 from d2wd_internal.manifest_sync_runs
    where id=p_run_id and status='running' and completed_at is null
  ) then
    raise exception 'manifest sync run % is not active',p_run_id;
  end if;

  for v_entry in select value from jsonb_array_elements(p_entries)
  loop
    if coalesce(v_entry->>'entity_type','') not in ('weapon','perk','pool')
       or nullif(v_entry->>'entity_id','') is null
       or nullif(v_entry->>'field_name','') is null
       or coalesce(v_entry->>'source_type','') not in (
         'bungie_manifest','historical_manifest','same_hash_catalogue_match','derived'
       )
       or coalesce(v_entry->>'confidence','') not in ('exact','corroborated','candidate') then
      raise exception 'invalid evidence record';
    end if;
  end loop;

  insert into d2wd_internal.catalogue_field_evidence (
    run_id,entity_type,entity_id,field_name,old_value,new_value,
    source_type,source_ref,source_version,confidence,review_status,notes
  )
  select
    p_run_id,e->>'entity_type',e->>'entity_id',e->>'field_name',
    e->'old_value',e->'new_value',e->>'source_type',e->>'source_ref',
    e->>'source_version',e->>'confidence','pending',e->>'notes'
  from jsonb_array_elements(p_entries) e;

  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

revoke all on function public.d2wd_start_manifest_sync(text,text,text,jsonb) from public, anon, authenticated;
revoke all on function public.d2wd_finish_manifest_sync(uuid,text,jsonb,jsonb,text) from public, anon, authenticated;
revoke all on function public.d2wd_get_manifest_sync_state() from public, anon, authenticated;
revoke all on function public.d2wd_record_sync_evidence(uuid,jsonb) from public, anon, authenticated;
grant execute on function public.d2wd_start_manifest_sync(text,text,text,jsonb) to service_role;
grant execute on function public.d2wd_finish_manifest_sync(uuid,text,jsonb,jsonb,text) to service_role;
grant execute on function public.d2wd_get_manifest_sync_state() to service_role;
grant execute on function public.d2wd_record_sync_evidence(uuid,jsonb) to service_role;
