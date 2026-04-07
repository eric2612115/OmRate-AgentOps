"""Compare backend market payloads against injected official snapshots (API comparator MVP)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
import uuid

from validators.browser_validation import BrowserValidationAdapter
from validators.manifest_loader import save_manifest
from validators.url_validation import compare_market_urls


class SupportsProtocolRunFlow(Protocol):
    def create_protocol_run(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def list_markets(
        self,
        *,
        protocol: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]: ...
    def attach_validation_evidence(
        self,
        protocol_run_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...
    def update_protocol_run(
        self,
        protocol_run_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...


_BUCKET_RANK = {"PASS": 0, "WARN": 1, "BUG": 2}


def aggregate_worst_bucket(buckets: Sequence[str]) -> str:
    if not buckets:
        return "PASS"
    return max(buckets, key=lambda b: _BUCKET_RANK.get(b, 0))


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metric_bucket_for_pair(
    backend_v: float | None,
    official_v: float | None,
    *,
    warn_abs_diff: float,
    bug_abs_diff: float,
) -> tuple[float | None, str]:
    if backend_v is None and official_v is None:
        return None, "PASS"
    if backend_v is None or official_v is None:
        return None, "BUG"
    diff = abs(backend_v - official_v)
    if diff <= warn_abs_diff:
        return diff, "PASS"
    if diff <= bug_abs_diff:
        return diff, "WARN"
    return diff, "BUG"


def compare_market_metrics(
    backend: dict[str, Any],
    official: dict[str, Any],
    *,
    apy_fields: Sequence[str] = ("supply_apy", "borrow_apy"),
    warn_abs_diff: float = 0.05,
    bug_abs_diff: float = 0.5,
) -> dict[str, Any]:
    """
    Compare numeric APY-style fields between backend and official snapshots.

    ``warn_abs_diff`` (inclusive): max absolute difference treated as PASS.
    Above that, up to ``bug_abs_diff`` -> WARN; larger -> BUG.
    """
    fields_out: list[dict[str, Any]] = []
    buckets: list[str] = []
    for name in apy_fields:
        bv = _float_or_none(backend.get(name))
        ov = _float_or_none(official.get(name))
        abs_diff, bucket = _metric_bucket_for_pair(
            bv,
            ov,
            warn_abs_diff=warn_abs_diff,
            bug_abs_diff=bug_abs_diff,
        )
        buckets.append(bucket)
        fields_out.append(
            {
                "field": name,
                "backend_value": bv,
                "official_value": ov,
                "abs_diff": abs_diff,
                "bucket": bucket,
            }
        )
    return {
        "fields": fields_out,
        "bucket": aggregate_worst_bucket(buckets),
    }


def _terminal_status_for_worst(worst: str) -> str:
    if worst == "BUG":
        return "failed"
    if worst == "WARN":
        return "degraded"
    return "success"


def _extract_markets_list(list_response: dict[str, Any]) -> list[dict[str, Any]]:
    if "data" in list_response and isinstance(list_response["data"], list):
        return list(list_response["data"])
    raw = list_response.get("markets")
    if isinstance(raw, list):
        return list(raw)
    return []


def build_protocol_validation_manifest(
    *,
    protocol: str,
    ruleset_version: str,
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return {
        "kind": "protocol_validation",
        "protocol": protocol,
        "ruleset_version": ruleset_version,
        "generated_at": generated_at,
    }


def run_protocol_validation(
    client: SupportsProtocolRunFlow,
    *,
    protocol: str,
    limit: int = 50,
    ruleset_version: str,
    manifest_path: str | Path,
    official_for_market: Callable[[dict[str, Any]], dict[str, Any]],
    apy_fields: Sequence[str] = ("supply_apy", "borrow_apy"),
    warn_abs_diff: float = 0.05,
    bug_abs_diff: float = 0.5,
    browser_adapter: BrowserValidationAdapter | None = None,
) -> dict[str, Any]:
    """
    Orchestration: write manifest, create protocol run, list markets, compare each market
    using ``official_for_market`` (injected, no live external fetch), attach summary
    evidence, optional per-market browser evidence (``source="browser"``), and set
    terminal phase/status on the run.
    """
    manifest = build_protocol_validation_manifest(
        protocol=protocol,
        ruleset_version=ruleset_version,
    )
    mpath = Path(manifest_path)
    save_manifest(mpath, manifest)
    manifest_id = mpath.stem

    create_body: dict[str, Any] = {
        "protocol_run_id": f"run_{uuid.uuid4().hex[:12]}",
        "protocol": protocol,
        "ruleset_version": ruleset_version,
        "manifest_id": manifest_id,
        "actor_type": "agent",
        "current_phase": "compare",
    }
    created = client.create_protocol_run(create_body)
    protocol_run_id = str(created.get("protocol_run_id") or created.get("id") or "")

    listed = client.list_markets(protocol=protocol, limit=limit)
    markets = _extract_markets_list(listed)

    comparisons: list[dict[str, Any]] = []
    row_buckets: list[str] = []
    for market in markets:
        official = official_for_market(market)
        m_cmp = compare_market_metrics(
            market,
            official,
            apy_fields=apy_fields,
            warn_abs_diff=warn_abs_diff,
            bug_abs_diff=bug_abs_diff,
        )
        u_cmp = compare_market_urls(
            official.get("expected_market_url"),
            market.get("market_url"),
        )
        row_worst = aggregate_worst_bucket([m_cmp["bucket"], u_cmp["bucket"]])
        row_buckets.append(row_worst)
        comparisons.append(
            {
                "market_id": market.get("market_id"),
                "metrics": m_cmp,
                "urls": u_cmp,
                "row_bucket": row_worst,
            }
        )

    worst = aggregate_worst_bucket(row_buckets) if row_buckets else "PASS"
    terminal = _terminal_status_for_worst(worst)

    evidence_payload = {
        "evidence_id": f"{manifest_id}-api-compare",
        "market_id": f"protocol::{protocol}",
        "source": "api_compare",
        "summary": f"{protocol} API compare worst bucket: {worst}",
        "metadata": {
            "protocol": protocol,
            "market_count": len(markets),
            "worst_bucket": worst,
            "comparisons": comparisons,
        },
        "actor_type": "agent",
    }
    client.attach_validation_evidence(protocol_run_id, evidence_payload)

    if browser_adapter is not None:
        for idx, market in enumerate(markets):
            outcome = browser_adapter.validate_market_url(market)
            mid = str(market.get("market_id") or "")
            evidence_id = f"{manifest_id}-browser-{mid or idx}"
            browser_meta: dict[str, Any] = {
                **outcome.metadata,
                "resolved_url": outcome.resolved_url,
                "status": outcome.status,
                "market_id": mid,
            }
            browser_evidence: dict[str, Any] = {
                "evidence_id": evidence_id,
                "market_id": mid,
                "source": "browser",
                "summary": outcome.summary,
                "metadata": browser_meta,
                "actor_type": "agent",
            }
            client.attach_validation_evidence(protocol_run_id, browser_evidence)

    update_body: dict[str, Any] = {
        "protocol": protocol,
        "manifest_id": manifest_id,
        "ruleset_version": ruleset_version,
        "current_phase": "completed",
        "terminal_status": terminal,
        "actor_type": "agent",
    }
    client.update_protocol_run(protocol_run_id, update_body)

    return {
        "protocol_run_id": protocol_run_id,
        "manifest_path": str(mpath.resolve()),
        "terminal_status": terminal,
        "worst_bucket": worst,
        "comparisons": comparisons,
    }
