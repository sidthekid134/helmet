# Helmet

Helmet is a read-only Sleeper fantasy-football intelligence system. It stores
point-in-time league and NFL data, produces deterministic draft and in-season
recommendations, and uses grounded AI analysis to explain misses and propose
reviewable policy changes.

## Setup

1. Install Python dependencies with `uv sync --dev`.
2. Copy `.env.example` to `.env`. The default `HELMET_PERSISTENCE_BACKEND=local`
   needs no external database.
3. Install the dashboard dependencies once with `npm install --prefix web`.
4. Run both the API and dashboard with `uv run helmet dev`.

The app is available at `http://localhost:3000`; the API listens on
`http://localhost:8000`. Press `Ctrl+C` once to stop both. Use `helmet dev
--help` to change either port or disable API auto-reload.

Sleeper's official API is read-only. Helmet never submits picks, lineups,
waivers, or trades.

## Persistence backends

Helmet reads and writes through one table contract with two interchangeable
backends, selected explicitly by `HELMET_PERSISTENCE_BACKEND`:

| Backend | Storage | Use |
| --- | --- | --- |
| `local` | SQLite at `HELMET_LOCAL_DATABASE_PATH` (default `data/helmet.db`) | Development without external services |
| `supabase` | Postgres via Supabase | Shared and production use |

The local backend enforces the same owner scoping, immutable audit columns, and
unique keys as the migration, so behavior matches Supabase. It is rejected when
`HELMET_ENVIRONMENT=production`, and selecting `supabase` without credentials
fails loudly rather than silently falling back to local storage.

Run `uv run helmet db status` to confirm which backend is active and where data
is stored. For Supabase, apply
`supabase/migrations/20260826200000_temporal_persistence.sql` first.

In local mode `HELMET_OWNER_USER_ID` may be any label, which is mapped to a
stable UUID. Supabase mode requires the real `auth.users` UUID.

## Research warm start

Run `uv run helmet research warm-start`. The command uses point-in-time
nflverse data from 2024–2025 and writes the versioned 2026 baseline to
`data/research/baseline_2026.json`. Unsupported hypotheses remain explicitly
marked as insufficient evidence.

## Verification

- Backend: `uv run pytest` and `uv run ruff check .`
- Frontend: `npm run lint && npm run typecheck && npm test && npm run build`
