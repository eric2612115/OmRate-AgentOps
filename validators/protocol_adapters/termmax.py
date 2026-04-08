"""Termmax REST API payload normalization."""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlparse

from validators.official_sources import ProtocolOfficialSource
from validators.protocol_adapters import OfficialMarketSnapshot, market_expected_url
from validators.protocol_adapters.live_fetch import managed_http_client, post_json


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


def _decimal(v: Any) -> float | None:
    if isinstance(v, dict):
        return _to_float(v.get("_val"))
    return _to_float(v)


class TermmaxAdapter:
    protocol_id = "termmax"
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
            raise ValueError("termmax: official_api_base is required")
        runtime = source.runtime_metadata or {}
        path = str(runtime.get("list_path") or "/market/list")
        transformed: list[dict[str, Any]] = []
        wanted_pool = _pool_hint_from_market(market)
        if not wanted_pool:
            raise ValueError("termmax: market_url missing pool hint")
        page = 1
        with managed_http_client(self._transport) as client:
            while True:
                payload = post_json(
                    client,
                    f"{api_base.rstrip('/')}{path}",
                    body={
                        "pagination": {"page": page, "perPage": 100},
                        "sort": [],
                        "filter": {"activity": "active"},
                    },
                )
                data = payload.get("data") or {}
                for item in data.get("items") or []:
                    entity = item.get("entity") or {}
                    pool_address = str(entity.get("address") or "").strip().lower()
                    if wanted_pool and pool_address != wanted_pool:
                        continue
                    transformed.append(
                        {
                            "chainId": entity.get("chainId"),
                            "marketId": item.get("pk", {}).get("marketAddress") or entity.get("address"),
                            "supplyApy": _decimal((entity.get("stats") or {}).get("lend", {}).get("apy")),
                            "borrowApy": _decimal((entity.get("stats") or {}).get("borrow", {}).get("apy")),
                            "tvlUsd": _decimal((entity.get("stats") or {}).get("lentValue")),
                            "borrowedUsd": _decimal((entity.get("stats") or {}).get("borrowedValue")),
                            "poolAddress": entity.get("address"),
                        }
                    )
                total_pages = int(data.get("totalPages") or 1)
                if transformed or page >= total_pages:
                    break
                page += 1
        return {"data": {"markets": transformed}}

    def normalize_snapshot(
        self,
        market: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> OfficialMarketSnapshot:
        protocol = str(market.get("protocol", "termmax"))
        chain_id = int(market["chain_id"])
        root = payload.get("data") or payload
        markets = root.get("markets") or []
        wanted_pool = _pool_hint_from_market(market)
        candidates = [
            m
            for m in markets
            if int(m.get("chainId", -1)) == chain_id
            and (
                not wanted_pool
                or str(m.get("poolAddress") or m.get("marketId") or "").strip().lower()
                == wanted_pool
            )
        ]
        chosen = candidates[0] if len(candidates) == 1 else None
        if not chosen and wanted_pool and candidates:
            chosen = candidates[0]
        if not chosen:
            if wanted_pool:
                raise ValueError("termmax: no matching market for pool in payload")
            raise ValueError("termmax: ambiguous chain-level market match without pool hint")

        supply = _to_float(chosen.get("supplyApy"))
        borrow = _to_float(chosen.get("borrowApy"))
        t_supply = _to_float(chosen.get("tvlUsd"))
        t_borrow = _to_float(chosen.get("borrowedUsd"))
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
            implied_apy=None,
            total_supply_usd=t_supply,
            total_borrow_usd=t_borrow,
            matched_by="chain_market",
            source_kind=self.source_kind,
            raw_metadata=meta,
        )


def _pool_hint_from_market(market: Mapping[str, Any]) -> str | None:
    raw_url = str(market.get("market_url") or "").strip()
    if not raw_url:
        return None
    parts = [part for part in urlparse(raw_url).path.split("/") if part]
    if "market" in parts:
        idx = parts.index("market")
        if idx + 2 < len(parts):
            return parts[idx + 2].strip().lower()
    if "earn" in parts:
        idx = parts.index("earn")
        if idx + 2 < len(parts):
            return parts[idx + 2].strip().lower()
    return None
