begin;

create extension if not exists pgcrypto;

create type public.ingestion_run_status as enum ('pending', 'running', 'succeeded', 'partial', 'failed');
create type public.review_status as enum ('pending', 'approved', 'rejected', 'superseded');
create type public.recommendation_status as enum ('draft', 'proposed', 'accepted', 'rejected', 'expired');
create type public.policy_status as enum ('draft', 'candidate', 'active', 'retired');

create or replace function public.set_audit_columns()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if tg_op = 'UPDATE' then
    if new.created_at is distinct from old.created_at
       or new.created_by is distinct from old.created_by
       or new.owner_user_id is distinct from old.owner_user_id then
      raise exception 'immutable audit columns cannot be changed';
    end if;
    new.updated_at := now();
    new.updated_by := auth.uid();
  end if;
  return new;
end;
$$;

create table public.app_users (
  id uuid primary key references auth.users(id) on delete cascade,
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  display_name text,
  timezone text not null default 'UTC',
  preferences jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  constraint app_users_owner_self check (id = owner_user_id),
  constraint app_users_preferences_object check (jsonb_typeof(preferences) = 'object')
);

create table public.ingestion_runs (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  source_system text not null,
  run_type text not null,
  idempotency_key text not null,
  status public.ingestion_run_status not null default 'pending',
  cursor_before jsonb,
  cursor_after jsonb,
  records_seen integer not null default 0 check (records_seen >= 0),
  records_written integer not null default 0 check (records_written >= 0),
  started_at timestamptz,
  completed_at timestamptz,
  error_code text,
  error_detail jsonb,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  constraint ingestion_run_times check (completed_at is null or (started_at is not null and completed_at >= started_at)),
  constraint ingestion_run_error check (status <> 'failed' or error_code is not null),
  constraint ingestion_run_metadata_object check (jsonb_typeof(metadata) = 'object'),
  unique (owner_user_id, idempotency_key)
);

create table public.source_observations (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  ingestion_run_id uuid references public.ingestion_runs(id) on delete set null,
  source_system text not null,
  source_entity_type text not null,
  source_entity_id text not null,
  observed_at timestamptz not null,
  effective_at timestamptz not null,
  content_hash text not null,
  payload jsonb not null,
  source_url text,
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  constraint source_observation_hash check (content_hash ~ '^[0-9a-f]{64}$'),
  constraint source_observation_payload_object check (jsonb_typeof(payload) = 'object'),
  unique (owner_user_id, source_system, source_entity_type, source_entity_id, effective_at, content_hash)
);

create table public.player_identities (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  canonical_name text not null,
  normalized_name text not null,
  sport text not null default 'football',
  position text,
  team_code text,
  birth_date date,
  active boolean not null default true,
  observed_at timestamptz not null,
  effective_at timestamptz not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  attributes jsonb not null default '{}'::jsonb check (jsonb_typeof(attributes) = 'object'),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  unique (owner_user_id, normalized_name, effective_at, content_hash)
);

create table public.player_external_ids (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  player_identity_id uuid not null references public.player_identities(id),
  source_system text not null,
  external_player_id text not null,
  confidence numeric(5,4) not null default 1 check (confidence between 0 and 1),
  valid_from timestamptz not null,
  valid_to timestamptz,
  observed_at timestamptz not null,
  effective_at timestamptz not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  constraint external_id_validity check (valid_to is null or valid_to > valid_from),
  unique (owner_user_id, source_system, external_player_id, valid_from)
);

create table public.player_mapping_reviews (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  source_system text not null,
  external_player_id text not null,
  proposed_player_identity_id uuid references public.player_identities(id),
  status public.review_status not null default 'pending',
  confidence numeric(5,4) check (confidence between 0 and 1),
  rationale text,
  reviewed_at timestamptz,
  reviewed_by uuid references auth.users(id),
  observed_at timestamptz not null,
  effective_at timestamptz not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  constraint mapping_review_resolution check (
    (status = 'pending' and reviewed_at is null and reviewed_by is null)
    or (status <> 'pending' and reviewed_at is not null and reviewed_by is not null)
  )
);

create table public.leagues (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  source_system text not null,
  external_league_id text not null,
  name text not null,
  season integer not null check (season between 2000 and 2200),
  settings jsonb not null default '{}'::jsonb check (jsonb_typeof(settings) = 'object'),
  observed_at timestamptz not null,
  effective_at timestamptz not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  unique (owner_user_id, source_system, external_league_id, season, effective_at)
);

create table public.league_members (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  league_id uuid not null references public.leagues(id) on delete cascade,
  external_manager_id text not null,
  team_name text not null,
  is_user_team boolean not null default false,
  observed_at timestamptz not null,
  effective_at timestamptz not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  unique (owner_user_id, league_id, external_manager_id, effective_at)
);

create table public.rosters (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  league_id uuid not null references public.leagues(id) on delete cascade,
  league_member_id uuid not null references public.league_members(id) on delete cascade,
  season integer not null check (season between 2000 and 2200),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  unique (owner_user_id, league_member_id, season)
);

create table public.roster_snapshots (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  roster_id uuid not null references public.rosters(id) on delete cascade,
  week integer not null check (week between 0 and 30),
  observed_at timestamptz not null,
  effective_at timestamptz not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  source_observation_id uuid references public.source_observations(id),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  unique (owner_user_id, roster_id, week, effective_at, content_hash)
);

create table public.roster_snapshot_players (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  roster_snapshot_id uuid not null references public.roster_snapshots(id) on delete cascade,
  player_identity_id uuid not null references public.player_identities(id),
  slot text not null,
  acquisition_type text,
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  unique (roster_snapshot_id, player_identity_id)
);

create table public.drafts (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  league_id uuid not null references public.leagues(id) on delete cascade,
  external_draft_id text,
  draft_type text not null,
  status text not null check (status in ('scheduled', 'in_progress', 'complete')),
  starts_at timestamptz,
  observed_at timestamptz not null,
  effective_at timestamptz not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  settings jsonb not null default '{}'::jsonb check (jsonb_typeof(settings) = 'object'),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  unique (owner_user_id, league_id, external_draft_id, effective_at)
);

create table public.draft_picks (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  draft_id uuid not null references public.drafts(id) on delete cascade,
  league_member_id uuid references public.league_members(id),
  player_identity_id uuid not null references public.player_identities(id),
  round integer not null check (round > 0),
  pick_in_round integer not null check (pick_in_round > 0),
  overall_pick integer not null check (overall_pick > 0),
  observed_at timestamptz not null,
  effective_at timestamptz not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  unique (draft_id, overall_pick),
  unique (draft_id, player_identity_id)
);

create table public.transactions (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  league_id uuid not null references public.leagues(id) on delete cascade,
  external_transaction_id text not null,
  transaction_type text not null check (transaction_type in ('add', 'drop', 'trade', 'waiver', 'commissioner')),
  status text not null,
  processed_at timestamptz,
  observed_at timestamptz not null,
  effective_at timestamptz not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  details jsonb not null default '{}'::jsonb check (jsonb_typeof(details) = 'object'),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  unique (owner_user_id, league_id, external_transaction_id, content_hash)
);

create table public.transaction_players (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  transaction_id uuid not null references public.transactions(id) on delete cascade,
  player_identity_id uuid not null references public.player_identities(id),
  from_member_id uuid references public.league_members(id),
  to_member_id uuid references public.league_members(id),
  action text not null check (action in ('add', 'drop', 'trade')),
  faab_amount numeric(12,2) check (faab_amount is null or faab_amount >= 0),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  constraint transaction_player_movement check (from_member_id is not null or to_member_id is not null),
  unique (transaction_id, player_identity_id, action)
);

create table public.weekly_player_stats (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  player_identity_id uuid not null references public.player_identities(id),
  source_system text not null,
  season integer not null check (season between 2000 and 2200),
  week integer not null check (week between 0 and 30),
  stat_line jsonb not null check (jsonb_typeof(stat_line) = 'object'),
  fantasy_points numeric(12,4),
  observed_at timestamptz not null,
  effective_at timestamptz not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  unique (owner_user_id, player_identity_id, source_system, season, week, effective_at, content_hash)
);

create table public.injuries (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  player_identity_id uuid not null references public.player_identities(id),
  source_system text not null,
  status text not null,
  body_part text,
  practice_status text,
  detail text,
  expected_return_at timestamptz,
  observed_at timestamptz not null,
  effective_at timestamptz not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  unique (owner_user_id, player_identity_id, source_system, effective_at, content_hash)
);

create table public.projections (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  player_identity_id uuid not null references public.player_identities(id),
  league_id uuid references public.leagues(id),
  source_system text not null,
  model_version text,
  season integer not null check (season between 2000 and 2200),
  week integer check (week between 0 and 30),
  projected_points numeric(12,4) not null,
  distribution jsonb check (distribution is null or jsonb_typeof(distribution) = 'object'),
  observed_at timestamptz not null,
  effective_at timestamptz not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  unique (owner_user_id, player_identity_id, league_id, source_system, season, week, effective_at, content_hash)
);

create table public.recommendations (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  league_id uuid not null references public.leagues(id) on delete cascade,
  policy_version_id uuid,
  recommendation_type text not null,
  status public.recommendation_status not null default 'draft',
  subject jsonb not null check (jsonb_typeof(subject) = 'object'),
  rationale jsonb not null check (jsonb_typeof(rationale) = 'object'),
  score numeric(12,6),
  expires_at timestamptz,
  observed_at timestamptz not null,
  effective_at timestamptz not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id)
);

create table public.recommendation_outcomes (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  recommendation_id uuid not null references public.recommendations(id) on delete cascade,
  action_taken boolean not null,
  outcome_type text not null,
  realized_value numeric(14,6),
  outcome jsonb not null default '{}'::jsonb check (jsonb_typeof(outcome) = 'object'),
  observed_at timestamptz not null,
  effective_at timestamptz not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  unique (owner_user_id, recommendation_id, outcome_type, effective_at, content_hash)
);

create table public.research_findings (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  source_observation_id uuid references public.source_observations(id),
  topic text not null,
  claim text not null,
  evidence jsonb not null check (jsonb_typeof(evidence) in ('object', 'array')),
  confidence numeric(5,4) not null check (confidence between 0 and 1),
  valid_from timestamptz not null,
  valid_to timestamptz,
  observed_at timestamptz not null,
  effective_at timestamptz not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  constraint research_validity check (valid_to is null or valid_to > valid_from)
);

create table public.error_patterns (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  pattern_key text not null,
  category text not null,
  description text not null,
  signature jsonb not null check (jsonb_typeof(signature) = 'object'),
  severity text not null check (severity in ('low', 'medium', 'high', 'critical')),
  first_observed_at timestamptz not null,
  last_observed_at timestamptz not null,
  occurrence_count integer not null default 1 check (occurrence_count > 0),
  observed_at timestamptz not null,
  effective_at timestamptz not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  constraint error_pattern_times check (last_observed_at >= first_observed_at),
  unique (owner_user_id, pattern_key, effective_at, content_hash)
);

create table public.error_attributions (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  error_pattern_id uuid not null references public.error_patterns(id) on delete cascade,
  ingestion_run_id uuid references public.ingestion_runs(id),
  recommendation_id uuid references public.recommendations(id),
  component text not null,
  root_cause text,
  responsibility_weight numeric(5,4) not null check (responsibility_weight between 0 and 1),
  evidence jsonb not null default '{}'::jsonb check (jsonb_typeof(evidence) = 'object'),
  observed_at timestamptz not null,
  effective_at timestamptz not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  constraint attribution_target check (ingestion_run_id is not null or recommendation_id is not null)
);

create table public.manager_profiles (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  league_member_id uuid not null references public.league_members(id) on delete cascade,
  sample_size integer not null default 0 check (sample_size >= 0),
  tendencies jsonb not null check (jsonb_typeof(tendencies) = 'object'),
  confidence numeric(5,4) not null check (confidence between 0 and 1),
  observed_at timestamptz not null,
  effective_at timestamptz not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  unique (owner_user_id, league_member_id, effective_at, content_hash)
);

create table public.policy_versions (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  policy_key text not null,
  version integer not null check (version > 0),
  status public.policy_status not null default 'draft',
  definition jsonb not null check (jsonb_typeof(definition) = 'object'),
  evaluation_metrics jsonb not null default '{}'::jsonb check (jsonb_typeof(evaluation_metrics) = 'object'),
  parent_policy_version_id uuid references public.policy_versions(id),
  observed_at timestamptz not null,
  effective_at timestamptz not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  unique (owner_user_id, policy_key, version),
  unique (owner_user_id, policy_key, content_hash)
);

alter table public.recommendations
  add constraint recommendations_policy_version_fk
  foreign key (policy_version_id) references public.policy_versions(id);

create table public.policy_promotions (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  policy_version_id uuid not null references public.policy_versions(id),
  from_status public.policy_status not null,
  to_status public.policy_status not null,
  promoted_at timestamptz not null,
  promoted_by uuid not null references auth.users(id),
  reason text not null,
  evidence jsonb not null default '{}'::jsonb check (jsonb_typeof(evidence) = 'object'),
  observed_at timestamptz not null,
  effective_at timestamptz not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  constraint policy_promotion_transition check (from_status <> to_status),
  unique (owner_user_id, policy_version_id, promoted_at)
);

create index ingestion_runs_owner_status_idx on public.ingestion_runs (owner_user_id, status, created_at desc);
create index source_observations_lookup_idx on public.source_observations (owner_user_id, source_system, source_entity_type, source_entity_id, observed_at desc);
create index source_observations_hash_idx on public.source_observations (content_hash);
create index player_identities_name_idx on public.player_identities (owner_user_id, normalized_name);
create index player_external_ids_lookup_idx on public.player_external_ids (owner_user_id, source_system, external_player_id) where valid_to is null;
create index player_mapping_reviews_pending_idx on public.player_mapping_reviews (owner_user_id, created_at) where status = 'pending';
create index leagues_current_idx on public.leagues (owner_user_id, season, effective_at desc);
create index roster_snapshots_current_idx on public.roster_snapshots (owner_user_id, roster_id, week, effective_at desc);
create index draft_picks_player_idx on public.draft_picks (owner_user_id, player_identity_id);
create index transactions_timeline_idx on public.transactions (owner_user_id, league_id, effective_at desc);
create index weekly_stats_player_idx on public.weekly_player_stats (owner_user_id, player_identity_id, season, week, effective_at desc);
create index injuries_current_idx on public.injuries (owner_user_id, player_identity_id, effective_at desc);
create index projections_lookup_idx on public.projections (owner_user_id, league_id, season, week, projected_points desc);
create index recommendations_status_idx on public.recommendations (owner_user_id, league_id, status, effective_at desc);
create index research_findings_topic_idx on public.research_findings (owner_user_id, topic, effective_at desc);
create index error_patterns_key_idx on public.error_patterns (owner_user_id, pattern_key, last_observed_at desc);
create index manager_profiles_current_idx on public.manager_profiles (owner_user_id, league_member_id, effective_at desc);
create unique index policy_versions_one_active_idx on public.policy_versions (owner_user_id, policy_key) where status = 'active';

do $$
declare
  table_name text;
begin
  foreach table_name in array array[
    'app_users', 'ingestion_runs', 'source_observations', 'player_identities',
    'player_external_ids', 'player_mapping_reviews', 'leagues', 'league_members',
    'rosters', 'roster_snapshots', 'roster_snapshot_players', 'drafts', 'draft_picks',
    'transactions', 'transaction_players', 'weekly_player_stats', 'injuries',
    'projections', 'recommendations', 'recommendation_outcomes', 'research_findings',
    'error_patterns', 'error_attributions', 'manager_profiles', 'policy_versions',
    'policy_promotions'
  ]
  loop
    execute format('alter table public.%I enable row level security', table_name);
    execute format(
      'create policy %I on public.%I for all to authenticated using (owner_user_id = auth.uid()) with check (owner_user_id = auth.uid())',
      table_name || '_owner_access',
      table_name
    );
    execute format(
      'create trigger %I before update on public.%I for each row execute function public.set_audit_columns()',
      table_name || '_audit_columns',
      table_name
    );
  end loop;
end;
$$;

commit;
