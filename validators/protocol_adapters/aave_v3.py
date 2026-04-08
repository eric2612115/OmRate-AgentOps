"""AAVE v3 GraphQL (`api.v3.aave.com`) payload normalization."""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from validators.official_sources import ProtocolOfficialSource
from validators.protocol_adapters import (
    OfficialMarketSnapshot,
    market_debt_symbol,
    market_expected_url,
)
from validators.protocol_adapters.live_fetch import managed_http_client, post_graphql_json

_AAVE_MARKETS_QUERY = """
query GetAaveV3Markets($chainIds: [Int!]!) {
  markets(request: { chainIds: $chainIds }) {
    chain { chainId }
    reserves {
      underlyingToken {
        address
        symbol
        decimals
      }
      supplyInfo {
        apy { formatted }
        total { value }
      }
      borrowInfo {
        apy { formatted }
        total { usd }
      }
    }
  }
}
"""


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


def _apy_field(v: Any) -> float | None:
    if isinstance(v, dict):
        for k in ("raw", "value"):
            if k in v:
                return _to_float(v.get(k))
    return _to_float(v)


def _percent_to_decimal(v: Any) -> float | None:
    raw = _apy_field(v)
    if raw is None:
        return None
    return raw / 100.0


def _usd(v: Any) -> float | None:
    if isinstance(v, dict):
        return _to_float(v.get("usd") if "usd" in v else v.get("value"))
    return _to_float(v)


class AaveV3Adapter:
    protocol_id = "aave_v3"
    source_kind = "graphql"

    def __init__(self, transport: Any = None) -> None:
        self._transport = transport

    def fetch_snapshot(
        self,
        market: Mapping[str, Any],
        source: ProtocolOfficialSource,
    ) -> Mapping[str, Any]:
        api_base = source.official_api_base
        if not api_base:
            raise ValueError("aave_v3: official_api_base is required")
        chain_id = int(market["chain_id"])
        with managed_http_client(self._transport) as client:
            live_payload = post_graphql_json(
                client,
                api_base,
                query=_AAVE_MARKETS_QUERY,
                variables={"chainIds": [chain_id]},
            )
        transformed_markets: list[dict[str, Any]] = []
        for market_row in (live_payload.get("data") or {}).get("markets") or []:
            reserves: list[dict[str, Any]] = []
            for reserve in market_row.get("reserves") or []:
                supply_info = reserve.get("supplyInfo") or {}
                borrow_info = reserve.get("borrowInfo") or {}
                reserves.append(
                    {
                        "underlyingToken": reserve.get("underlyingToken") or {},
                        "liquidityAPY": {
                            "raw": _percent_to_decimal((supply_info.get("apy") or {}).get("formatted"))
                        },
                        "variableBorrowAPY": {
                            "raw": _percent_to_decimal((borrow_info.get("apy") or {}).get("formatted"))
                        },
                        "size": {"usd": _usd(supply_info.get("total"))},
                        "borrow": {"usd": _usd(borrow_info.get("total"))},
                    }
                )
            transformed_markets.append(
                {
                    "chain": market_row.get("chain") or {"chainId": chain_id},
                    "reserves": reserves,
                }
            )
        return {"data": {"markets": transformed_markets}}

    def normalize_snapshot(
        self,
        market: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> OfficialMarketSnapshot:
        protocol = str(market.get("protocol", "aave_v3"))
        chain_id = int(market["chain_id"])
        symbol = market_debt_symbol(market, default="USDC")
        underlying_address = _underlying_asset_hint(market)
        data = payload.get("data") or {}
        markets = data.get("markets") or []
        chosen = None
        for m in markets:
            ch = (m.get("chain") or {}).get("chainId")
            if ch is not None and int(ch) != chain_id:
                continue
            for r in m.get("reserves") or []:
                ut = r.get("underlyingToken") or {}
                address = str(ut.get("address") or "").strip().lower()
                sym = ut.get("symbol")
                if underlying_address and address == underlying_address:
                    chosen = r
                    break
                if not underlying_address and sym and str(sym).upper() == symbol.upper():
                    chosen = r
                    break
            if chosen:
                break
        if not chosen:
            raise ValueError("aave_v3: no matching reserve in payload")

        supply = _apy_field(chosen.get("liquidityAPY"))
        borrow = _apy_field(chosen.get("variableBorrowAPY"))
        t_supply = _usd(chosen.get("size"))
        t_borrow = _usd(chosen.get("borrow"))
        meta = {"reserve": chosen, "chain_id": chain_id, "symbol": symbol}
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


def _underlying_asset_hint(market: Mapping[str, Any]) -> str | None:
    raw_url = str(market.get("market_url") or "").strip()
    if not raw_url:
        return None
    query_value = (parse_qs(urlparse(raw_url).query).get("underlyingAsset") or [None])[0]
    if query_value is None:
        return None
    text = str(query_value).strip().lower()
    return text or None
