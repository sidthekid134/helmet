begin;

-- Expectimax draft-plan trees (see helmet.draft). One `draft_plans` row per
-- generated plan; `draft_plan_nodes` and `draft_plan_candidates` are its
-- immutable children, produced once by the planner and never edited in
-- place -- regenerating a plan creates a new `draft_plans` row rather than
-- mutating an existing tree.
--
-- `chosen_player_id` / `player_id` are stored as the raw projection player_id
-- (an nflverse gsis_id), not a foreign key to `player_identities`. Nothing in
-- Helmet yet ingests nflverse identities into `player_identities` /
-- `player_external_ids`, so a foreign key here would either fail plan
-- generation outright or silently null out the player -- both worse than
-- being honest that this identity link does not exist yet. Add the FK in a
-- follow-up migration once that ingestion exists.

create table public.draft_plans (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  league_id uuid references public.leagues(id) on delete cascade,
  draft_id uuid references public.drafts(id) on delete cascade,
  research_policy_version_id uuid references public.policy_versions(id),
  num_teams integer not null check (num_teams >= 2),
  my_slot integer not null check (my_slot >= 1),
  rounds integer not null check (rounds >= 1),
  seed bigint not null,
  simulation_iterations integer not null check (simulation_iterations >= 1),
  node_count integer not null check (node_count >= 0),
  status text not null default 'active' check (status in ('active', 'stale', 'superseded')),
  config jsonb not null check (jsonb_typeof(config) = 'object'),
  observed_at timestamptz not null,
  effective_at timestamptz not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  constraint draft_plans_slot_within_teams check (my_slot <= num_teams),
  unique (owner_user_id, content_hash)
);

create table public.draft_plan_nodes (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  plan_id uuid not null references public.draft_plans(id) on delete cascade,
  parent_node_id uuid references public.draft_plan_nodes(id) on delete cascade,
  node_key text not null,
  depth integer not null check (depth >= 0),
  overall_pick integer check (overall_pick > 0),
  round integer check (round > 0),
  chosen_player_id text,
  chosen_player_name text,
  chosen_player_team text,
  chosen_player_position text,
  chosen_archetype text,
  board_state_hash text not null,
  reach_probability numeric(7,6) not null check (reach_probability between 0 and 1),
  roster_player_ids jsonb not null check (jsonb_typeof(roster_player_ids) = 'array'),
  ev numeric(12,4) not null,
  ev_floor numeric(12,4) not null,
  ev_ceiling numeric(12,4) not null,
  rationale jsonb not null default '[]'::jsonb check (jsonb_typeof(rationale) = 'array'),
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  constraint draft_plan_nodes_root_has_no_pick check (
    (parent_node_id is null and overall_pick is null and chosen_player_id is null)
    or (parent_node_id is not null and overall_pick is not null and chosen_player_id is not null)
  ),
  unique (plan_id, node_key)
);

create table public.draft_plan_candidates (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null default auth.uid() references auth.users(id),
  plan_id uuid not null references public.draft_plans(id) on delete cascade,
  parent_node_id uuid not null references public.draft_plan_nodes(id) on delete cascade,
  player_id text not null,
  player_name text not null,
  player_team text not null,
  player_position text not null,
  archetype text,
  survival_probability numeric(7,6) not null check (survival_probability between 0 and 1),
  marginal_value numeric(12,4) not null,
  rank integer not null check (rank > 0),
  expanded boolean not null default false,
  child_node_id uuid references public.draft_plan_nodes(id) on delete set null,
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid() references auth.users(id),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id),
  constraint draft_plan_candidates_child_requires_expanded check (
    (expanded and child_node_id is not null) or (not expanded and child_node_id is null)
  ),
  unique (parent_node_id, player_id)
);

create index draft_plans_lookup_idx on public.draft_plans (owner_user_id, league_id, status, effective_at desc);
create index draft_plan_nodes_plan_idx on public.draft_plan_nodes (owner_user_id, plan_id, depth);
create index draft_plan_nodes_parent_idx on public.draft_plan_nodes (owner_user_id, parent_node_id);
create index draft_plan_candidates_parent_idx on public.draft_plan_candidates (owner_user_id, parent_node_id, rank);

do $$
declare
  table_name text;
begin
  foreach table_name in array array[
    'draft_plans', 'draft_plan_nodes', 'draft_plan_candidates'
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
