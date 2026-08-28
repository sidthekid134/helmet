from pathlib import Path

import polars as pl
from fastapi.testclient import TestClient

from helmet.api import create_app
from helmet.flows.pipelines import idempotency_key
from helmet.research import FindingStatus, write_report
from helmet.research.warm_start import build_warm_start


def _stats() -> pl.DataFrame:
    rows = []
    for index in range(40):
        touches = 360 if index < 15 else 200
        next_efficiency = 0.8 if index < 15 else 1.2
        rows.extend(
            [
                {
                    "player_id": f"p{index}",
                    "season": 2024,
                    "position": "RB",
                    "carries": touches,
                    "receptions": 0,
                    "fantasy_points_ppr": touches,
                },
                {
                    "player_id": f"p{index}",
                    "season": 2025,
                    "position": "RB",
                    "carries": 200,
                    "receptions": 0,
                    "fantasy_points_ppr": 200 * next_efficiency,
                },
            ]
        )
    return pl.DataFrame(rows)


class FakeNflverse:
    def load(self, *_args, **_kwargs) -> pl.DataFrame:
        return _stats()


def test_warm_start_is_no_lookahead_and_promotes_supported_signal(tmp_path: Path) -> None:
    report = build_warm_start(client=FakeNflverse())  # type: ignore[arg-type]
    assert report.training_seasons == (2024, 2025)
    assert report.findings[0].status is FindingStatus.SUPPORTED
    assert "rb_350_touch_next_year_points_per_touch" in report.promoted_modifiers
    output = tmp_path / "report.json"
    write_report(report, output)
    assert output.exists()


def test_warm_start_rejects_target_in_training_window() -> None:
    try:
        build_warm_start(seasons=(2025, 2026), target_season=2026, client=FakeNflverse())  # type: ignore[arg-type]
    except ValueError as exc:
        assert "precede" in str(exc)
    else:
        raise AssertionError("lookahead season was accepted")


def test_idempotency_key_is_stable_and_period_specific() -> None:
    first = idempotency_key("sleeper", "league", 2026, "week-1")
    assert first == idempotency_key("sleeper", "league", 2026, "week-1")
    assert first != idempotency_key("sleeper", "league", 2026, "week-2")


def test_api_health_does_not_require_external_services() -> None:
    response = TestClient(create_app()).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
