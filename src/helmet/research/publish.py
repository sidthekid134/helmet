"""Publish warm-start research results into queryable persistence.

`build_warm_start` (in `helmet.research.warm_start`) only produces a report in
memory. This module is the boundary that turns that report into rows the rest
of the system can query:

* one `research_findings` row per hypothesis, so `GET /v1/research` and the
  grounded chat agent can cite real findings instead of an empty table.
* one `policy_versions` row for the promoted projection modifiers, so the
  projection builder has a single "active" definition to read instead of a
  file nothing loads.

Publishing is idempotent by content hash: re-running the same warm start does
not create duplicate findings or duplicate policy versions. Reactivating a
previously retired modifier set (a rollback) is also handled, per the
blueprint's requirement that policy changes be reversible.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from helmet.persistence import PersistenceContext
from helmet.repositories import (
    PolicyPromotionRepository,
    PolicyVersionRepository,
    ResearchFindingRepository,
    canonical_content_hash,
)

from .warm_start import WarmStartReport

PROJECTION_MODIFIERS_POLICY_KEY = "projection_modifiers"


def publish_findings(
    report: WarmStartReport, db: PersistenceContext
) -> list[dict[str, Any]]:
    """Write one research_findings row per hypothesis, skipping exact repeats."""
    repository = ResearchFindingRepository(db.client, db.owner_user_id)
    written: list[dict[str, Any]] = []
    for finding in report.findings:
        evidence = [
            {
                "source_system": source,
                "narrative": finding.evidence,
                "status": finding.status.value,
                "effect_size": finding.effect_size,
                "sample_size": finding.sample_size,
                "training_seasons": list(report.training_seasons),
                "target_season": report.target_season,
            }
            for source in finding.required_sources
        ]
        content_hash = canonical_content_hash(
            {"topic": finding.hypothesis, "status": finding.status.value, "evidence": evidence}
        )
        existing = repository.list(filters={"content_hash": content_hash}, limit=1)
        if existing:
            written.append(existing[0])
            continue
        written.append(
            repository.create(
                {
                    "topic": finding.hypothesis,
                    "claim": f"{finding.hypothesis}: {finding.status.value.replace('_', ' ')}",
                    "evidence": evidence,
                    "confidence": finding.confidence,
                    "valid_from": report.generated_at,
                    "observed_at": report.generated_at,
                    "effective_at": report.generated_at,
                    "content_hash": content_hash,
                }
            )
        )
    return written


def publish_promoted_modifiers(
    report: WarmStartReport, db: PersistenceContext
) -> dict[str, Any]:
    """Publish `report.promoted_modifiers` as the active projection-modifier policy.

    Registering a new modifier name here does nothing by itself. See
    `helmet.projections.modifiers` for the applier registry that must also
    recognize the name before it can change a projection.
    """
    versions = PolicyVersionRepository(db.client, db.owner_user_id)
    promotions = PolicyPromotionRepository(db.client, db.owner_user_id)
    definition = dict(sorted(report.promoted_modifiers.items()))
    content_hash = canonical_content_hash(definition)
    now = datetime.now(UTC).isoformat()

    matching = versions.list(
        filters={"policy_key": PROJECTION_MODIFIERS_POLICY_KEY, "content_hash": content_hash},
        limit=1,
    )
    if matching and matching[0]["status"] == "active":
        return matching[0]

    prior_active = versions.list(
        filters={"policy_key": PROJECTION_MODIFIERS_POLICY_KEY, "status": "active"},
        limit=100,
    )
    if len(prior_active) > 1:
        raise RuntimeError(
            f"multiple active policy versions already exist for {PROJECTION_MODIFIERS_POLICY_KEY}"
        )

    if matching:
        # This exact modifier set was published before and later retired; reactivate
        # it rather than violating the (policy_key, content_hash) uniqueness constraint.
        activated = versions.update(matching[0]["id"], {"status": "active"})
        reason = "reactivated: matches a previously retired policy version"
    else:
        all_versions = versions.list(
            filters={"policy_key": PROJECTION_MODIFIERS_POLICY_KEY}, order_by="version", limit=1000
        )
        next_version = max((row["version"] for row in all_versions), default=0) + 1
        activated = versions.create(
            {
                "policy_key": PROJECTION_MODIFIERS_POLICY_KEY,
                "version": next_version,
                "status": "active",
                "definition": definition,
                "evaluation_metrics": {
                    "supported_findings": sum(
                        1 for finding in report.findings if finding.status.value == "supported"
                    ),
                    "total_findings": len(report.findings),
                },
                "observed_at": now,
                "effective_at": now,
                "content_hash": content_hash,
            }
        )
        reason = "warm-start promotion threshold met"

    for row in prior_active:
        if row["id"] == activated["id"]:
            continue
        versions.update(row["id"], {"status": "retired"})
        promotions.create(
            {
                "policy_version_id": row["id"],
                "from_status": "active",
                "to_status": "retired",
                "promoted_at": now,
                "promoted_by": db.owner_user_id,
                "reason": f"superseded by version {activated['version']}",
                "evidence": {"superseded_by": activated["id"]},
                "observed_at": now,
                "effective_at": now,
                "content_hash": canonical_content_hash(
                    {"policy_version_id": row["id"], "to_status": "retired", "at": now}
                ),
            }
        )
    promotions.create(
        {
            "policy_version_id": activated["id"],
            "from_status": "draft" if not matching else "retired",
            "to_status": "active",
            "promoted_at": now,
            "promoted_by": db.owner_user_id,
            "reason": reason,
            "evidence": {"model_version": report.model_version},
            "observed_at": now,
            "effective_at": now,
            "content_hash": canonical_content_hash(
                {"policy_version_id": activated["id"], "to_status": "active", "at": now}
            ),
        }
    )
    return activated


def active_projection_modifiers(db: PersistenceContext) -> dict[str, float]:
    """Return the currently active promoted projection modifiers, if any."""
    versions = PolicyVersionRepository(db.client, db.owner_user_id)
    rows = versions.list(
        filters={"policy_key": PROJECTION_MODIFIERS_POLICY_KEY, "status": "active"}, limit=2
    )
    if not rows:
        return {}
    if len(rows) > 1:
        raise RuntimeError(
            f"multiple active policy versions for {PROJECTION_MODIFIERS_POLICY_KEY}"
        )
    return dict(rows[0]["definition"])


def publish_warm_start(report: WarmStartReport, db: PersistenceContext) -> dict[str, Any]:
    """Publish both findings and the promoted-modifier policy version."""
    return {
        "findings": publish_findings(report, db),
        "policy_version": publish_promoted_modifiers(report, db),
    }
