"""Euler v2 Goldsky subgraph payload normalization."""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlparse

from validators.official_sources import ProtocolOfficialSource
from validators.protocol_adapters import OfficialMarketSnapshot, market_expected_url
from validators.protocol_adapters.live_fetch import managed_http_client, post_graphql_json

_EULER_VAULTS_QUERY = """
query GetVaults($first: Int!, $skip: Int!) {
  eulerVaults(
    first: $first
    skip: $skip
    where: { state_not: null }
    orderBy: blockNumber
    orderDirection: desc
  ) {
    id
    state {
      supplyApy
      borrowApy
    }
  }
}
"""

_EULER_PAGE_SIZE = 1000
_RAY = 10**27


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


class EulerV2Adapter:
    protocol_id = "euler_v2"
    source_kind = "subgraph"

    def __init__(self, transport: Any = None) -> None:
        self._transport = transport

    def fetch_snapshot(
        self,
        market: Mapping[str, Any],
        source: ProtocolOfficialSource,
    ) -> Mapping[str, Any]:
        api_base = source.official_api_base
        if not api_base:
            raise ValueError("euler_v2: official_api_base is required")
        chain_id = int(market["chain_id"])
        runtime = source.runtime_metadata or {}
        subgraph_name = str((runtime.get("chain_subgraphs") or {}).get(chain_id) or "").strip()
        if not subgraph_name:
            raise ValueError(f"euler_v2: missing chain_subgraphs mapping for chain_id={chain_id}")
        url = f"{api_base.rstrip('/')}/{subgraph_name}/latest/gn"
        want_id = _wanted_vault_id(market)
        if not want_id:
            raise ValueError("euler_v2: market_url missing vault id hint")
        transformed: list[dict[str, Any]] = []
        skip = 0
        with managed_http_client(self._transport) as client:
            while True:
                payload = post_graphql_json(
                    client,
                    url,
                    query=_EULER_VAULTS_QUERY,
                    variables={"first": _EULER_PAGE_SIZE, "skip": skip},
                )
                page_vaults = (payload.get("data") or {}).get("eulerVaults") or []
                for vault in page_vaults:
                    vault_id = str(vault.get("id") or "").lower()
                    if want_id and vault_id != want_id:
                        continue
                    state = vault.get("state") or {}
                    transformed.append(
                        {
                            "id": vault.get("id"),
                            "chainId": str(chain_id),
                            "supplyAPY": _ray_to_apy(state.get("supplyApy")),
                            "borrowAPY": _ray_to_apy(state.get("borrowApy")),
                            "totalAssetsUsd": None,
                            "totalBorrowsUsd": None,
                        }
                    )
                if len(page_vaults) < _EULER_PAGE_SIZE:
                    break
                skip += _EULER_PAGE_SIZE
        return {"data": {"vaults": transformed}}

    def normalize_snapshot(
        self,
        market: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> OfficialMarketSnapshot:
        protocol = str(market.get("protocol", "euler_v2"))
        chain_id = int(market["chain_id"])
        data = payload.get("data") or {}
        vaults = data.get("vaults") or []
        want_id = _wanted_vault_id(market)
        candidates = [
            v
            for v in vaults
            if int(str(v.get("chainId", chain_id))) == chain_id
            and (not want_id or str(v.get("id") or "").strip().lower() == want_id)
        ]
        chosen = candidates[0] if len(candidates) == 1 else None
        if not chosen and want_id and candidates:
            chosen = candidates[0]
        if not chosen:
            if want_id:
                raise ValueError("euler_v2: no matching vault for market in payload")
            raise ValueError("euler_v2: ambiguous chain-level vault match without vault id")

        supply = _to_float(chosen.get("supplyAPY"))
        borrow = _to_float(chosen.get("borrowAPY"))
        t_supply = _to_float(chosen.get("totalAssetsUsd"))
        t_borrow = _to_float(chosen.get("totalBorrowsUsd"))
        meta = {"vault": chosen}
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
            matched_by="vault_chain",
            source_kind=self.source_kind,
            raw_metadata=meta,
        )


def _ray_to_apy(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return int(v) / _RAY
    except (TypeError, ValueError):
        return None


def _wanted_vault_id(market: Mapping[str, Any]) -> str | None:
    for candidate in (
        market.get("vault_id"),
        (market.get("extra") or {}).get("vault_address"),
        market.get("pool_address"),
    ):
        text = str(candidate or "").strip().lower()
        if text:
            return text
    raw_url = str(market.get("market_url") or "").strip()
    if not raw_url:
        return None
    parts = [part for part in urlparse(raw_url).path.split("/") if part]
    if "vault" in parts:
        idx = parts.index("vault")
        if idx + 1 < len(parts):
            text = parts[idx + 1].strip().lower()
            if text:
                return text
    if "positions" in parts:
        idx = parts.index("positions")
        if idx + 2 < len(parts):
            text = parts[idx + 2].strip().lower()
            if text:
                return text
    return None
