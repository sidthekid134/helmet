from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from helmet.agents import AttributionCategory, AttributionService, Evidence


class Messages:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    async def create(self, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.payload)])


def client(payload: str) -> SimpleNamespace:
    return SimpleNamespace(messages=Messages(payload))


@pytest.mark.asyncio
async def test_attribution_service_accepts_only_grounded_evidence() -> None:
    service = AttributionService(
        api_key="unused",
        model="test",
        client=client(
            '{"category":"role_change","explanation":"Usage fell",'
            '"evidence_ids":["e1"],"confidence":0.8}'
        ),
    )
    result = await service.attribute(
        player_id="p1",
        projected_points=20,
        actual_points=8,
        evidence=(
            Evidence(
                evidence_id="e1",
                source="snap-counts",
                claim="Snap rate fell to 40%",
                observed_at=datetime(2026, 9, 10, tzinfo=UTC),
            ),
        ),
    )
    assert result.category is AttributionCategory.ROLE_CHANGE


@pytest.mark.asyncio
async def test_attribution_service_rejects_unknown_evidence_id() -> None:
    service = AttributionService(
        api_key="unused",
        model="test",
        client=client(
            '{"category":"role_change","explanation":"Usage fell",'
            '"evidence_ids":["invented"],"confidence":0.8}'
        ),
    )
    with pytest.raises(ValueError, match="missing evidence"):
        await service.attribute(
            player_id="p1",
            projected_points=20,
            actual_points=8,
            evidence=(
                Evidence(
                    evidence_id="e1",
                    source="snap-counts",
                    claim="Snap rate fell to 40%",
                    observed_at=datetime(2026, 9, 10, tzinfo=UTC),
                ),
            ),
        )
