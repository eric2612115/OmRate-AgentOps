"""Pendle Core API v2 payload normalization."""

from __future__ import annotations

from urllib.parse import urlparse
from typing import Any, Mapping

from validators.official_sources import ProtocolOfficialSource
from validators.protocol_adapters import OfficialMarketSnapshot, market_expected_url
from validators.protocol_adapters.live_fetch import get_json, managed_http_client


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _usd_liq(entry: Mapping[str, Any]) -> float | None:
    liq = entry.get("liquidity")
    if isinstance(liq, dict):
        return _to_float(liq.get("usd"))
    return None


class PendleAdapter:
    protocol_id = "pendle"
    source_kind = "rest"

    def __init__(self, transport: Any = None) -> None:
        self._transport = transport

    def fetch_snapshot(
        self,
        market: Mapping[str, Any],
        source: ProtocolOfficialSource,
    ) -> Mapping[str, Any]:
        api_base = source.official_api_base
        if not api_base:
            raise ValueError("pendle: official_api_base is required")
        chain_id = int(market["chain_id"])
        runtime = source.runtime_metadata or {}
        path_template = str(runtime.get("market_list_path_template") or "/v1/{chain_id}/markets")
        url = f"{api_base.rstrip('/')}{path_template.format(chain_id=chain_id)}"
        wanted_address = _market_address_hint(market)
        if not wanted_address:
            raise ValueError("pendle: market_url missing market address hint")
        rows_out: list[dict[str, Any]] = []
        skip = 0
        with managed_http_client(self._transport) as client:
            while True:
                payload = get_json(
                    client,
                    url,
                    params={"limit": 100, "skip": skip, "isExpired": False},
                )
                page_rows = payload.get("results") or payload.get("data") or []
                if not isinstance(page_rows, list):
                    break
                rows = list(page_rows)
                if wanted_address:
                    rows = [
                        row
                        for row in rows
                        if str(row.get("address") or "").strip().lower() == wanted_address
                    ]
                rows_out.extend(rows)
                if wanted_address and rows_out:
                    break
                if len(page_rows) < 100:
                    break
                skip += 100
        return {"data": rows_out}

    def normalize_snapshot(
        self,
        market: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> OfficialMarketSnapshot:
        protocol = str(market.get("protocol", "pendle"))
        chain_id = int(market["chain_id"])
        addr = _market_address_hint(market)
        rows = payload.get("data")
        if not isinstance(rows, list):
            rows = payload.get("markets") or []
        if not isinstance(rows, list):
            rows = []
        candidates = [
            row
            for row in rows
            if int(row.get("chainId", -1)) == chain_id
            and (
                not addr
                or str(row.get("address") or "").strip().lower() == addr
            )
        ]
        chosen = candidates[0] if len(candidates) == 1 else None
        if not chosen and addr and candidates:
            chosen = candidates[0]
        if not chosen:
            if addr:
                raise ValueError("pendle: no matching market address in payload")
            raise ValueError("pendle: ambiguous chain-level market match without address hint")

        implied = _to_float(chosen.get("impliedApy"))
        pt = chosen.get("pt") or {}
        yt = chosen.get("yt") or {}
        supply = _to_float(pt.get("apy"))
        borrow = _to_float(yt.get("apy"))
        liq_usd = _usd_liq(chosen)
        meta = {"market": chosen}
        return OfficialMarketSnapshot(
            protocol=protocol,
            chain_id=chain_id,
            market_type=str(market.get("market_type", "unknown")),
            expected_market_url=market_expected_url(market),
            supply_apy=supply,
            borrow_apy=borrow,
            net_supply_apy=supply,
            net_borrow_apy=borrow,
            implied_apy=implied,
            total_supply_usd=liq_usd,
            total_borrow_usd=None,
            matched_by="chain_market",
            source_kind=self.source_kind,
            raw_metadata=meta,
        )


def _market_address_hint(market: Mapping[str, Any]) -> str | None:
    raw = str(
        market.get("market_address")
        or market.get("pool_address")
        or ""
    ).strip().lower()
    if raw:
        return raw
    raw_url = str(market.get("market_url") or "").strip()
    if not raw_url:
        return None
    parts = [part for part in urlparse(raw_url).path.split("/") if part]
    if "markets" in parts:
        idx = parts.index("markets")
        if idx + 1 < len(parts):
            text = parts[idx + 1].strip().lower()
            if text:
                return text
    return None
