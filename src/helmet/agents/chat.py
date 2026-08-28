"""Source-grounded conversational interface."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from anthropic import AsyncAnthropic


class GroundedChatService:
    """Answers from supplied deterministic tool results and cites record IDs."""

    def __init__(self, *, api_key: str, model: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def answer(
        self,
        message: str,
        *,
        context: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> str:
        if not message.strip():
            raise ValueError("message cannot be empty")
        record_count = sum(len(rows) for rows in context.values())
        if record_count == 0:
            raise ValueError("grounded chat requires at least one retrieved record")
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=900,
            system=(
                "You are Helmet, a fantasy football decision assistant. Use only the "
                "provided records. Never calculate projections or invent missing facts. "
                "Cite supporting record IDs in square brackets. State when evidence is "
                "insufficient. All actions are recommendations; Sleeper remains read-only."
            ),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Question:\n{message}\n\nRetrieved records:\n"
                        f"{json.dumps(context, default=str, sort_keys=True)}"
                    ),
                }
            ],
        )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ).strip()
        if not text:
            raise RuntimeError("Anthropic returned no text")
        return text
