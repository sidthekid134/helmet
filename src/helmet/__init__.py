"""Helmet command-line entry point."""

from __future__ import annotations

import argparse
from pathlib import Path

# A standard PPR scoring block, used only as the default payload for
# `helmet leagues seed-test` so the draft planner can be exercised without a
# real Sleeper league. Real leagues always get their scoring settings from
# Sleeper itself via `POST /v1/connections`.
_DEFAULT_TEST_SCORING = {
    "pass_yd": 0.04,
    "pass_td": 4.0,
    "pass_int": -2.0,
    "rush_yd": 0.1,
    "rush_td": 6.0,
    "rec": 1.0,
    "rec_yd": 0.1,
    "rec_td": 6.0,
    "fum_lost": -2.0,
}
_DEFAULT_TEST_ROSTER_POSITIONS = (
    "QB",
    "RB",
    "RB",
    "WR",
    "WR",
    "TE",
    "FLEX",
    "DEF",
    "K",
    "BN",
    "BN",
    "BN",
    "BN",
    "BN",
    "BN",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="helmet")
    subcommands = parser.add_subparsers(dest="command", required=True)

    api = subcommands.add_parser("api", help="Run the FastAPI service")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)

    dev = subcommands.add_parser("dev", help="Run the API and web dashboard together")
    dev.add_argument("--host", default="127.0.0.1")
    dev.add_argument("--api-port", type=int, default=8000)
    dev.add_argument("--web-port", type=int, default=3000)
    dev.add_argument("--no-reload", action="store_true")

    leagues = subcommands.add_parser("leagues", help="Manage connected leagues")
    leagues_commands = leagues.add_subparsers(dest="leagues_command", required=True)
    seed_test = leagues_commands.add_parser(
        "seed-test",
        help="Insert a synthetic local league so the draft planner can be tested without Sleeper",
    )
    seed_test.add_argument("--league-id", default="test-league")
    seed_test.add_argument("--name", default="Test League")
    seed_test.add_argument("--season", type=int, default=2026)
    seed_test.add_argument("--num-teams", type=int, default=12)
    seed_test.add_argument(
        "--scoring",
        type=str,
        default=None,
        help="JSON object of Sleeper-style scoring settings; defaults to standard PPR",
    )

    research = subcommands.add_parser("research", help="Run historical research")
    research_commands = research.add_subparsers(dest="research_command", required=True)
    warm_start = research_commands.add_parser(
        "warm-start", help="Build a point-in-time 2026 baseline"
    )
    warm_start.add_argument(
        "--output",
        type=Path,
        default=Path("data/research/baseline_2026.json"),
    )
    warm_start.add_argument(
        "--no-publish",
        action="store_true",
        help="Skip publishing findings and promoted modifiers to persistence",
    )
    warm_start.add_argument(
        "--no-plans",
        action="store_true",
        help="Skip precomputing draft plans for every connected league and slot",
    )

    draft = subcommands.add_parser("draft", help="Generate and inspect draft plans")
    draft_commands = draft.add_subparsers(dest="draft_command", required=True)
    plan = draft_commands.add_parser(
        "plan", help="Generate a draft plan tree for a connected league"
    )
    plan.add_argument("--league-id", required=True, help="External (Sleeper) league id")
    plan.add_argument("--my-slot", type=int, required=True)
    plan.add_argument(
        "--num-teams",
        type=int,
        default=None,
        help="Defaults to the league's own total_rosters from Sleeper",
    )
    plan.add_argument(
        "--rounds",
        type=int,
        default=None,
        help="Defaults to the length of the league's roster_positions",
    )
    plan.add_argument(
        "--roster-targets",
        default=None,
        help='JSON object of your target roster depth, e.g. {"QB":1,"RB":5,"WR":6,"TE":2}; '
        "defaults to a shape derived from the league's roster_positions",
    )
    plan.add_argument(
        "--starters-per-team",
        default=None,
        help='JSON object of league-wide starters, e.g. {"QB":1,"RB":2,"WR":2,"TE":1}; '
        "defaults to a shape derived from the league's roster_positions",
    )
    plan.add_argument("--simulation-iterations", type=int, default=150)
    plan.add_argument("--seed", type=int, default=20260827)

    database = subcommands.add_parser("db", help="Inspect the configured persistence backend")
    database_commands = database.add_subparsers(dest="db_command", required=True)
    database_commands.add_parser("status", help="Open the backend and report where data lives")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "api":
        import uvicorn

        uvicorn.run("helmet.api:app", host=args.host, port=args.port)
        return
    if args.command == "dev":
        from helmet.dev import run_dev

        raise SystemExit(
            run_dev(
                host=args.host,
                api_port=args.api_port,
                web_port=args.web_port,
                reload=not args.no_reload,
            )
        )
    if args.command == "leagues" and args.leagues_command == "seed-test":
        import json
        from datetime import UTC, datetime

        from helmet.config import get_settings
        from helmet.domain import League
        from helmet.persistence import open_persistence
        from helmet.repositories import LeagueRepository, canonical_content_hash

        scoring_settings = (
            json.loads(args.scoring) if args.scoring is not None else dict(_DEFAULT_TEST_SCORING)
        )
        league = League(
            league_id=args.league_id,
            name=args.name,
            season=args.season,
            status="in_season",
            total_rosters=args.num_teams,
            roster_positions=_DEFAULT_TEST_ROSTER_POSITIONS,
            scoring_settings=scoring_settings,
        )
        db = open_persistence(get_settings())
        repository = LeagueRepository(db.client, db.owner_user_id)
        existing = repository.list(
            filters={"external_league_id": league.league_id, "season": league.season}, limit=1
        )
        if existing:
            raise SystemExit(
                f"league {league.league_id} (season {league.season}) is already seeded; "
                f"id={existing[0]['id']}"
            )
        now = datetime.now(UTC).isoformat()
        payload = league.model_dump(mode="json")
        row = repository.create(
            {
                "source_system": "sleeper",
                "external_league_id": league.league_id,
                "name": league.name,
                "season": league.season,
                "settings": payload,
                "observed_at": now,
                "effective_at": now,
                "content_hash": canonical_content_hash(payload),
            }
        )
        print(
            f"Seeded test league {row['external_league_id']} "
            f"(id={row['id']}) for {row['season']}"
        )
        return
    if args.command == "research" and args.research_command == "warm-start":
        from helmet.research import build_warm_start, publish_warm_start, write_report

        report = build_warm_start()
        write_report(report, args.output)
        print(f"Wrote {report.model_version} warm-start report to {args.output}")
        if not args.no_publish or not args.no_plans:
            from helmet.persistence import open_persistence

            db = open_persistence()
            if not args.no_publish:
                result = publish_warm_start(report, db)
                print(
                    f"Published {len(result['findings'])} findings; "
                    f"active projection_modifiers policy version "
                    f"{result['policy_version']['version']}"
                )
            if not args.no_plans:
                from helmet.draft import precompute_all_draft_plans
                from helmet.repositories import LeagueRepository

                if not LeagueRepository(db.client, db.owner_user_id).list(limit=1):
                    print("No connected leagues; skipping draft plan precomputation")
                else:
                    plans = precompute_all_draft_plans(db)
                    created = sum(1 for row in plans if row["created"])
                    print(
                        f"Precomputed {len(plans)} draft plans across every connected "
                        f"league and slot ({created} new, {len(plans) - created} already "
                        "cached by content hash)"
                    )
        return
    if args.command == "draft" and args.draft_command == "plan":
        import json

        from helmet.config import get_settings
        from helmet.draft import derive_draft_shape, generate_draft_plan
        from helmet.persistence import open_persistence
        from helmet.projections import ProjectionSettings
        from helmet.repositories import LeagueRepository

        db = open_persistence(get_settings())
        league_rows = LeagueRepository(db.client, db.owner_user_id).list(
            filters={"external_league_id": args.league_id}, limit=1
        )
        if not league_rows:
            raise SystemExit(
                f"league {args.league_id} is not connected; connect it via the API first"
            )
        league_row = league_rows[0]
        settings = league_row["settings"]
        season = league_row["season"]
        shape = derive_draft_shape(settings["roster_positions"])

        result = generate_draft_plan(
            db=db,
            sleeper_scoring_settings=settings["scoring_settings"],
            num_teams=args.num_teams or int(settings["total_rosters"]),
            my_slot=args.my_slot,
            rounds=args.rounds or shape.rounds,
            roster_targets=(
                json.loads(args.roster_targets) if args.roster_targets else shape.roster_targets
            ),
            starters_per_team=(
                json.loads(args.starters_per_team)
                if args.starters_per_team
                else shape.starters_per_team
            ),
            projection_settings=ProjectionSettings(
                target_season=season, lookback_seasons=tuple(range(season - 2, season))
            ),
            league_id=league_row["id"],
            seed=args.seed,
            simulation_iterations=args.simulation_iterations,
        )
        plan = result["plan"]
        verb = "Created" if result["created"] else "Reused existing"
        print(f"{verb} plan {plan['id']} with {plan['node_count']} nodes")
        return
    if args.command == "db" and args.db_command == "status":
        from helmet.config import get_settings
        from helmet.persistence import open_persistence

        settings = get_settings()
        persistence = open_persistence(settings)
        print(f"backend: {persistence.backend}")
        print(f"owner:   {persistence.owner_user_id}")
        if persistence.backend == "local":
            print(f"database: {settings.local_database_path}")
        else:
            print(f"database: {settings.supabase_url}")
        return
    raise RuntimeError("unhandled command")
