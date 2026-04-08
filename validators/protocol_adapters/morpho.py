"""Morpho GraphQL (`api.morpho.org`) payload normalization."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse
from typing import Any, Mapping

from validators.official_sources import ProtocolOfficialSource
from validators.protocol_adapters import (
    OfficialMarketSnapshot,
    market_debt_symbol,
    market_expected_url,
)
from validators.protocol_adapters.live_fetch import managed_http_client, post_graphql_json

_MORPHO_MARKETS_QUERY = """
query GetMarkets($first: Int!, $skip: Int!, $chainIds: [Int!], $uniqueKeys: [String!]) {
  markets(
    first: $first
    skip: $skip
    orderBy: SupplyAssetsUsd
    orderDirection: Desc
    where: {
      chainId_in: $chainIds
      uniqueKey_in: $uniqueKeys
    }
  ) {
    items {
      marketId
      uniqueKey
      loanAsset {
        address
        symbol
        decimals
        chain { id }
      }
      collateralAsset {
        address
        symbol
        decimals
        chain { id }
      }
      state {
        supplyApy
        borrowApy
        netSupplyApy
        netBorrowApy
        avgSupplyApy
        avgBorrowApy
        avgNetSupplyApy
        avgNetBorrowApy
        supplyAssetsUsd
        borrowAssetsUsd
      }
    }
  }
}
"""

_PAGE_SIZE = 100


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


def _state_rate(state: Mapping[str, Any], primary: str, fallback: str) -> float | None:
    if primary in state:
        value = _to_float(state.get(primary))
        if value is not None:
            return value
    return _to_float(state.get(fallback))


class MorphoAdapter:
    protocol_id = "morpho"
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
            raise ValueError("morpho: official_api_base is required")
        chain_id = int(market["chain_id"])
        wanted_key = _market_unique_key(market)
        if not wanted_key:
            raise ValueError("morpho: market_url missing unique market key hint")
        with managed_http_client(self._transport) as client:
            payload = post_graphql_json(
                client,
                api_base,
                query=_MORPHO_MARKETS_QUERY,
                variables={
                    "first": _PAGE_SIZE,
                    "skip": 0,
                    "chainIds": [chain_id],
                    "uniqueKeys": [wanted_key],
                },
            )
        items = (((payload.get("data") or {}).get("markets") or {}).get("items") or [])
        return {"data": {"markets": {"items": items}}}

    def normalize_snapshot(
        self,
        market: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> OfficialMarketSnapshot:
        protocol = str(market.get("protocol", "morpho"))
        chain_id = int(market["chain_id"])
        symbol = market_debt_symbol(market, default="USDC")
        wanted_key = _market_unique_key(market)
        data = payload.get("data") or {}
        markets = (data.get("markets") or {})
        items = markets.get("items") or []
        candidates = []
        for it in items:
            if wanted_key and str(it.get("uniqueKey") or it.get("marketId") or "").lower() != wanted_key:
                continue
            la = it.get("loanAsset") or {}
            if str(la.get("symbol", "")).upper() == symbol.upper():
                candidates.append(it)
        if not candidates:
            raise ValueError("morpho: no matching market item in payload")
        if wanted_key:
            chosen = candidates[0]
        elif len(candidates) == 1:
            chosen = candidates[0]
        else:
            raise ValueError("morpho: ambiguous symbol match without unique market key")

        st = chosen.get("state") or {}
        supply = _state_rate(st, "supplyApy", "avgSupplyApy")
        borrow = _state_rate(st, "borrowApy", "avgBorrowApy")
        t_supply = _to_float(st.get("supplyAssetsUsd"))
        t_borrow = _to_float(st.get("borrowAssetsUsd"))
        meta = {"market": chosen, "chain_id": chain_id}
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
            matched_by="loan_asset_symbol",
            source_kind=self.source_kind,
            raw_metadata=meta,
        )


def _market_unique_key(market: Mapping[str, Any]) -> str | None:
    raw_url = str(market.get("market_url") or "").strip()
    if not raw_url:
        return None
    parsed = urlparse(raw_url)
    query_key = (parse_qs(parsed.query).get("id") or [None])[0]
    if query_key:
        return str(query_key).strip().lower()
    parts = [part for part in parsed.path.split("/") if part]
    if "market" in parts:
        idx = parts.index("market")
        if idx + 1 < len(parts):
            return parts[idx + 1].strip().lower()
    return None
