"""Anthropic-backed attribution constrained to retrieved evidence."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from anthropic import AsyncAnthropic

from .learning import AttributionCategory, Evidence, GroundedAttribution


class AttributionService:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: Any | None = None,
    ) -> None:
        self._client = client or AsyncAnthropic(api_key=api_key)
        self._model = model

    async def attribute(
        self,
        *,
        player_id: str,
        projected_points: float,
        actual_points: float,
        evidence: Sequence[Evidence],
    ) -> GroundedAttribution:
        if not player_id.strip():
            raise ValueError("player_id cannot be empty")
        if not evidence:
            raise ValueError("attribution requires retrieved evidence")
        categories = [category.value for category in AttributionCategory]
        prompt = {
            "player_id": player_id,
            "projected_points": projected_points,
            "actual_points": actual_points,
            "allowed_categories": categories,
            "evidence": [
                {
                    "evidence_id": item.evidence_id,
                    "source": item.source,
                    "claim": item.claim,
                    "observed_at": item.observed_at.isoformat(),
                }
                for item in evidence
            ],
            "required_output": {
                "category": "one allowed category",
                "explanation": "grounded explanation",
                "evidence_ids": ["one or more supplied IDs"],
                "confidence": "number from 0 to 1",
            },
        }
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=500,
            system=(
                "Attribute the projection miss using only supplied evidence. Pure data "
                "limitations belong in data_quality. Return one JSON object and no prose. "
                "Never cite an evidence ID that was not supplied."
            ),
            messages=[{"role": "user", "content": json.dumps(prompt, sort_keys=True)}],
        )
        raw = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        try:
            payload = json.loads(raw)
            attribution = GroundedAttribution(
                category=AttributionCategory(payload["category"]),
                explanation=str(payload["explanation"]),
                evidence_ids=tuple(payload["evidence_ids"]),
                confidence=float(payload["confidence"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"invalid attribution response: {exc}") from exc
        attribution.validate_grounding(evidence)
        return attribution
