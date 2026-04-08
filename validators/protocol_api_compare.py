"""Compare backend market payloads against injected official snapshots (API comparator MVP)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
import uuid

from validators.browser_validation import (
    BROWSER_STATUS_BLOCKED,
    BROWSER_STATUS_FAIL,
    BrowserValidationAdapter,
)
from validators.official_sources import get_protocol_source
from validators.manifest_loader import save_manifest
from validators.protocol_adapters import (
    UnknownProtocolAdapterError,
    build_protocol_adapter,
    list_registered_protocol_ids,
)
from validators.report_schema import LiveValidationReport, LiveValidationRow, build_live_validation_report
from validators.sampling import chain_key_for_market, sample_markets_by_chain, sampling_metadata
from validators.url_validation import compare_market_urls


class SupportsListMarkets(Protocol):
    def list_protocols(self) -> dict[str, Any]: ...

    def list_markets(
        self,
        *,
        protocol: str | None = None,
        chain_id: int | None = None,
        limit: int | None = None,
        is_expired: bool | None = None,
        is_disabled: bool | None = None,
    ) -> dict[str, Any]: ...


class SupportsProtocolRunFlow(Protocol):
    def create_protocol_run(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def list_protocols(self) -> dict[str, Any]: ...
    def list_markets(
        self,
        *,
        protocol: str | None = None,
        chain_id: int | None = None,
        limit: int | None = None,
        is_expired: bool | None = None,
        is_disabled: bool | None = None,
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


def extract_markets_from_list_response(list_response: dict[str, Any]) -> list[dict[str, Any]]:
    """Public helper for matrix runners (same shape as :func:`run_protocol_validation` expects)."""
    return _extract_markets_list(list_response)


def compare_market_types(backend: dict[str, Any], official: dict[str, Any]) -> dict[str, Any]:
    """``market_type`` alignment; empty defaults to ``primary`` on both sides."""
    b = str(backend.get("market_type") or "").strip() or "primary"
    o = str(official.get("market_type") or "").strip() or "primary"
    if b == o:
        return {"bucket": "PASS", "backend": b, "official": o}
    return {"bucket": "BUG", "backend": b, "official": o}


def matrix_final_bucket(
    browser_status: str,
    api_bucket: str,
    url_bucket: str,
    type_bucket: str,
) -> str:
    if browser_status == BROWSER_STATUS_BLOCKED:
        return "BLOCKED"
    if browser_status == BROWSER_STATUS_FAIL:
        return "FAIL"
    for b in (api_bucket, url_bucket, type_bucket):
        if b != "PASS":
            return "FAIL"
    return "PASS"


def _annotate_sample_ranks(markets: list[dict[str, Any]]) -> list[tuple[dict[str, Any], int]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for m in markets:
        groups.setdefault(chain_key_for_market(m), []).append(m)
    for key in groups:
        groups[key].sort(key=lambda x: str(x.get("market_id") or ""))
    out: list[tuple[dict[str, Any], int]] = []
    for ck in sorted(groups.keys()):
        for rank, m in enumerate(groups[ck], start=1):
            out.append((m, rank))
    return out


def _chain_id_int(market: dict[str, Any]) -> int:
    cid = market.get("chain_id")
    if cid is None:
        return 0
    try:
        return int(cid)
    except (TypeError, ValueError):
        return 0


def _error_row(
    *,
    protocol: str,
    market: dict[str, Any],
    sample_rank: int,
    stage: str,
) -> LiveValidationRow:
    mid = str(market.get("market_id") or "")
    raw_url = market.get("market_url")
    resolved_url = str(raw_url).strip() if raw_url is not None and str(raw_url).strip() else None
    return LiveValidationRow(
        protocol=protocol,
        chain_id=_chain_id_int(market),
        market_id=mid or f"unknown-{sample_rank}",
        sample_rank=sample_rank,
        expected_url=resolved_url,
        resolved_url=resolved_url,
        browser_status="NONE",
        api_bucket="BUG",
        url_bucket="PASS",
        type_bucket="PASS",
        final_bucket="FAIL",
        evidence_refs=(
            f"{protocol}:{mid or 'row'}:{stage}",
            f"{protocol}:{mid or 'row'}:error",
        ),
    )


def _protocol_chain_ids_from_backend(
    client: SupportsListMarkets,
    *,
    protocols: Sequence[str],
) -> dict[str, list[int]]:
    response = client.list_protocols()
    rows = response.get("data")
    if not isinstance(rows, list):
        return {}
    requested = set(protocols)
    out: dict[str, list[int]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        protocol_id = str(row.get("id") or "").strip()
        if protocol_id not in requested:
            continue
        chain_ids: list[int] = []
        for raw in row.get("chains") or []:
            try:
                chain_ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        if chain_ids:
            out[protocol_id] = chain_ids
    return out


def _dedupe_markets_by_id(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for market in markets:
        market_id = str(market.get("market_id") or "")
        if market_id and market_id in seen:
            continue
        if market_id:
            seen.add(market_id)
        out.append(market)
    return out


def _emit_progress(progress_log: Callable[[str], None] | None, message: str) -> None:
    if progress_log is not None:
        progress_log(message)


def _close_browser_adapter(browser_adapter: BrowserValidationAdapter | None) -> None:
    if browser_adapter is None:
        return
    close = getattr(browser_adapter, "close", None)
    if callable(close):
        close()


def run_live_validation_matrix(
    client: SupportsListMarkets,
    *,
    run_id: str,
    ruleset_version: str,
    official_data_mode: str = "live",
    browser_runtime_mode: str = "off",
    protocols: Sequence[str] | None = None,
    limit: int = 50,
    per_chain_limit: int = 5,
    official_payload_provider: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    browser_adapter: BrowserValidationAdapter | None = None,
    dry_run: bool = False,
    apy_fields: Sequence[str] = ("supply_apy", "borrow_apy"),
    warn_abs_diff: float = 0.05,
    bug_abs_diff: float = 0.5,
    adapter_transport: Any = None,
    progress_log: Callable[[str], None] | None = None,
) -> LiveValidationReport:
    """
    Multi-protocol matrix: list markets per protocol, deterministic sample, API/URL/type compare,
    optional browser status, emit :class:`~validators.report_schema.LiveValidationReport`.
    """
    proto_list = list(protocols) if protocols is not None else list(list_registered_protocol_ids())
    rows_out: list[LiveValidationRow] = []
    protocol_chain_ids = _protocol_chain_ids_from_backend(client, protocols=proto_list)

    try:
        for protocol in proto_list:
            try:
                adapter = build_protocol_adapter(protocol, transport=adapter_transport)
            except UnknownProtocolAdapterError:
                continue
            source = get_protocol_source(protocol)
            _emit_progress(progress_log, f"[protocol:start] {protocol}")

            raw_markets: list[dict[str, Any]] = []
            chain_ids = protocol_chain_ids.get(protocol) or [None]
            for chain_id in chain_ids:
                listed = client.list_markets(
                    protocol=protocol,
                    chain_id=chain_id,
                    limit=limit,
                    is_expired=False,
                    is_disabled=False,
                )
                chain_rows = _extract_markets_list(listed)
                raw_markets.extend(chain_rows)
                _emit_progress(
                    progress_log,
                    f"[protocol:chain] {protocol} chain_id={chain_id if chain_id is not None else 'all'} rows={len(chain_rows)}",
                )
            raw_markets = _dedupe_markets_by_id(raw_markets)
            markets = sample_markets_by_chain(
                raw_markets,
                per_chain_limit=per_chain_limit,
            )
            _emit_progress(
                progress_log,
                f"[protocol:sampled] {protocol} sampled={len(markets)} raw={len(raw_markets)}",
            )
            ranked = _annotate_sample_ranks(markets)

            for market, sample_rank in ranked:
                market_id = str(market.get("market_id") or f"unknown-{sample_rank}")
                _emit_progress(
                    progress_log,
                    f"[market:start] {protocol} chain_id={_chain_id_int(market)} market_id={market_id} rank={sample_rank}",
                )
                try:
                    if official_payload_provider is not None:
                        payload = official_payload_provider(protocol, market)
                    else:
                        payload = dict(adapter.fetch_snapshot(market, source))
                    snap = adapter.normalize_snapshot(market, payload)
                except Exception:
                    rows_out.append(
                        _error_row(
                            protocol=protocol,
                            market=market,
                            sample_rank=sample_rank,
                            stage="official-fetch",
                        )
                    )
                    _emit_progress(
                        progress_log,
                        f"[market:done] {protocol} chain_id={_chain_id_int(market)} market_id={market_id} final=FAIL stage=official-fetch-error",
                    )
                    continue
                official = snap.as_dict()
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
                t_cmp = compare_market_types(market, official)

                if browser_adapter is not None:
                    bout = browser_adapter.validate_market_url(market)
                    browser_status = str(bout.status)
                    resolved_url = bout.resolved_url or market.get("market_url")
                else:
                    browser_status = "NONE"
                    resolved_url = market.get("market_url")

                final = matrix_final_bucket(
                    browser_status,
                    m_cmp["bucket"],
                    u_cmp["bucket"],
                    t_cmp["bucket"],
                )
                mid = str(market.get("market_id") or "")
                exp_u = official.get("expected_market_url")
                if exp_u is not None and str(exp_u).strip():
                    expected_url: str | None = str(exp_u).strip()
                else:
                    raw_exp = u_cmp.get("expected")
                    expected_url = str(raw_exp).strip() if raw_exp is not None and str(raw_exp).strip() else None
                res_u = resolved_url
                resolved_str: str | None = str(res_u).strip() if res_u is not None and str(res_u).strip() else None
                rows_out.append(
                    LiveValidationRow(
                        protocol=protocol,
                        chain_id=_chain_id_int(market),
                        market_id=mid or f"unknown-{sample_rank}",
                        sample_rank=sample_rank,
                        expected_url=expected_url,
                        resolved_url=resolved_str,
                        browser_status=browser_status,
                        api_bucket=m_cmp["bucket"],
                        url_bucket=u_cmp["bucket"],
                        type_bucket=t_cmp["bucket"],
                        final_bucket=final,
                        evidence_refs=(
                            f"{protocol}:{mid or 'row'}:metrics",
                            f"{protocol}:{mid or 'row'}:urls",
                            f"{protocol}:{mid or 'row'}:type",
                        ),
                    )
                )
                _emit_progress(
                    progress_log,
                    f"[market:done] {protocol} chain_id={_chain_id_int(market)} market_id={market_id} final={final} browser={browser_status} api={m_cmp['bucket']} url={u_cmp['bucket']} type={t_cmp['bucket']}",
                )
    finally:
        _close_browser_adapter(browser_adapter)

    return build_live_validation_report(
        run_id=run_id,
        ruleset_version=ruleset_version,
        rows=rows_out,
        official_data_mode=official_data_mode,
        browser_runtime_mode=browser_runtime_mode,
        dry_run=dry_run,
    )


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
    per_chain_limit: int = 5,
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

    protocol_chain_ids = _protocol_chain_ids_from_backend(client, protocols=[protocol])
    raw_markets: list[dict[str, Any]] = []
    chain_ids = protocol_chain_ids.get(protocol) or [None]
    for chain_id in chain_ids:
        listed = client.list_markets(
            protocol=protocol,
            chain_id=chain_id,
            limit=limit,
            is_expired=False,
            is_disabled=False,
        )
        raw_markets.extend(_extract_markets_list(listed))
    raw_markets = _dedupe_markets_by_id(raw_markets)
    markets = sample_markets_by_chain(
        raw_markets,
        per_chain_limit=per_chain_limit,
    )
    sample_meta = sampling_metadata(raw_markets, markets)

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
            **sample_meta,
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
        **sample_meta,
    }
