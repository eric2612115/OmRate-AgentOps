"""Venus REST (`api.venus.io`) payload normalization."""

from __future__ import annotations

from urllib.parse import urlparse
from typing import Any, Mapping

from validators.official_sources import ProtocolOfficialSource
from validators.protocol_adapters import (
    OfficialMarketSnapshot,
    market_debt_symbol,
    market_expected_url,
)
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


class VenusAdapter:
    protocol_id = "venus"
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
            raise ValueError("venus: official_api_base is required")
        chain_id = int(market["chain_id"])
        market_type = str(market.get("market_type") or "").strip().lower()
        runtime = source.runtime_metadata or {}
        with managed_http_client(self._transport) as client:
            if market_type == "isolated":
                payload = get_json(
                    client,
                    f"{api_base.rstrip('/')}{runtime.get('isolated_pools_path', '/pools')}",
                    params={"chainId": chain_id, "limit": 100},
                )
                flattened: list[dict[str, Any]] = []
                for pool in payload.get("result") or []:
                    for item in pool.get("markets") or []:
                        flattened.append(
                            {
                                "underlyingSymbol": item.get("underlyingSymbol") or item.get("symbol"),
                                "chainId": chain_id,
                                "supplyApy": _to_float(
                                    item.get("supplyApyDecimal") or item.get("supplyApy")
                                ),
                                "borrowApy": _to_float(
                                    item.get("borrowApyDecimal") or item.get("borrowApy")
                                ),
                                "totalSupplyUsd": _cents_to_usd(item.get("totalSupplyUnderlyingCents")),
                                "totalBorrowUsd": _cents_to_usd(item.get("totalBorrowCents")),
                                "poolAddress": pool.get("address"),
                            }
                        )
                return {"result": flattened}

            payload = get_json(
                client,
                f"{api_base.rstrip('/')}{runtime.get('core_pool_path', '/markets/core-pool')}",
                params={"chainId": chain_id},
            )
            flattened: list[dict[str, Any]] = []
            for item in payload.get("result") or []:
                flattened.append(
                    {
                        "underlyingSymbol": item.get("underlyingSymbol") or item.get("symbol"),
                        "chainId": chain_id,
                        "supplyApy": _to_float(item.get("supplyApyDecimal") or item.get("supplyApy")),
                        "borrowApy": _to_float(item.get("borrowApyDecimal") or item.get("borrowApy")),
                        "totalSupplyUsd": _cents_to_usd(item.get("totalSupplyUnderlyingCents")),
                        "totalBorrowUsd": _cents_to_usd(item.get("totalBorrowCents")),
                        "poolAddress": item.get("poolComptrollerAddress"),
                    }
                )
            return {"result": flattened}

    def normalize_snapshot(
        self,
        market: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> OfficialMarketSnapshot:
        protocol = str(market.get("protocol", "venus"))
        chain_id = int(market["chain_id"])
        symbol = market_debt_symbol(market, default="USDC")
        wanted_pool = _pool_hint_from_market(market)
        rows = payload.get("result")
        if not isinstance(rows, list):
            rows = payload.get("data") or []
        if not isinstance(rows, list):
            rows = []
        candidates = [
            row
            for row in rows
            if str(row.get("underlyingSymbol", "")).upper() == symbol.upper()
            and (not wanted_pool or str(row.get("poolAddress") or "").strip().lower() == wanted_pool)
            and ((row.get("chainId") is None) or int(str(row.get("chainId"))) == chain_id)
        ]
        chosen = candidates[0] if len(candidates) == 1 else None
        if not chosen and wanted_pool and candidates:
            chosen = candidates[0]
        if not chosen:
            if wanted_pool:
                raise ValueError("venus: no matching market row for pool in payload")
            if candidates:
                raise ValueError("venus: ambiguous symbol match without pool hint")
            raise ValueError("venus: no matching market row in payload")

        supply = _to_float(chosen.get("supplyApy"))
        borrow = _to_float(chosen.get("borrowApy"))
        t_supply = _to_float(chosen.get("totalSupplyUsd"))
        t_borrow = _to_float(chosen.get("totalBorrowUsd"))
        meta = {"row": chosen}
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
            matched_by="underlying_symbol",
            source_kind=self.source_kind,
            raw_metadata=meta,
        )


def _cents_to_usd(v: Any) -> float | None:
    cents = _to_float(v)
    if cents is None:
        return None
    return cents / 100.0


def _pool_hint_from_market(market: Mapping[str, Any]) -> str | None:
    raw_url = str(market.get("market_url") or "").strip()
    if not raw_url:
        return None
    parsed = urlparse(raw_url)
    parts = [part for part in parsed.path.split("/") if part]
    if "pool" in parts:
        idx = parts.index("pool")
        if idx + 1 < len(parts):
            return parts[idx + 1].strip().lower()
    if "market" in parts:
        idx = parts.index("market")
        if idx + 1 < len(parts):
            return parts[idx + 1].strip().lower()
    if "isolated-pools" in parts:
        idx = parts.index("isolated-pools")
        if idx + 1 < len(parts):
            return parts[idx + 1].strip().lower()
    fragment_parts = [part for part in parsed.fragment.split("/") if part]
    if "markets" in fragment_parts:
        idx = fragment_parts.index("markets")
        if idx + 1 < len(fragment_parts):
            return fragment_parts[idx + 1].split("?", 1)[0].strip().lower()
    return None
