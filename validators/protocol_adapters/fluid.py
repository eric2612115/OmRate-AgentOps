"""Fluid resolver / on-chain style payload normalization (no official HTTP base)."""

from __future__ import annotations

from urllib.parse import urlparse
from typing import Any, Mapping

from validators.official_sources import ProtocolOfficialSource
from validators.protocol_adapters import OfficialMarketSnapshot, market_expected_url
from validators.protocol_adapters.live_fetch import make_web3

_LENDING_RESOLVER_ABI = [
    {
        "inputs": [],
        "name": "getFTokensEntireData",
        "outputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenAddress", "type": "address"},
                    {"internalType": "bool", "name": "eip2612Deposits", "type": "bool"},
                    {"internalType": "bool", "name": "isNativeUnderlying", "type": "bool"},
                    {"internalType": "string", "name": "name", "type": "string"},
                    {"internalType": "string", "name": "symbol", "type": "string"},
                    {"internalType": "uint256", "name": "decimals", "type": "uint256"},
                    {"internalType": "address", "name": "asset", "type": "address"},
                    {"internalType": "uint256", "name": "totalAssets", "type": "uint256"},
                    {"internalType": "uint256", "name": "totalSupply", "type": "uint256"},
                    {"internalType": "uint256", "name": "convertToShares", "type": "uint256"},
                    {"internalType": "uint256", "name": "convertToAssets", "type": "uint256"},
                    {"internalType": "uint256", "name": "rewardsRate", "type": "uint256"},
                    {"internalType": "uint256", "name": "supplyRate", "type": "uint256"},
                ],
                "internalType": "struct Structs.FTokenDetails[]",
                "name": "",
                "type": "tuple[]",
            }
        ],
        "stateMutability": "view",
        "type": "function",
    }
]
_RATE_PRECISION = 1e4


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


def _field_or_none(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload.get(key)
    return None


class FluidAdapter:
    protocol_id = "fluid"
    source_kind = "onchain"

    def __init__(self, transport: Any = None) -> None:
        self._transport = transport

    def fetch_snapshot(
        self,
        market: Mapping[str, Any],
        source: ProtocolOfficialSource,
    ) -> Mapping[str, Any]:
        chain_id = int(market["chain_id"])
        runtime = source.runtime_metadata or {}
        resolver_addr = str((runtime.get("resolver_addresses") or {}).get(chain_id) or "").strip()
        if not resolver_addr:
            raise ValueError(f"fluid: missing resolver address for chain_id={chain_id}")
        w3 = make_web3(chain_id)
        contract = w3.eth.contract(
            address=w3.to_checksum_address(resolver_addr),
            abi=_LENDING_RESOLVER_ABI,
        )
        rows = contract.functions.getFTokensEntireData().call()
        want_pool = str(market.get("pool_address") or "").strip().lower()
        want_vault_index = _vault_index_hint(market)
        want_symbol = str(((market.get("debt_token") or {}).get("symbol") or market.get("symbol") or "")).strip().upper()
        matches: list[dict[str, Any]] = []
        for idx, row in enumerate(rows, start=1):
            if want_vault_index and idx != want_vault_index:
                continue
            token_address = str(row[0]).lower()
            raw_symbol = str(row[4] or "")
            derived_symbol = raw_symbol[1:] if raw_symbol.startswith("f") else raw_symbol
            if want_pool and token_address != want_pool:
                continue
            if want_symbol and derived_symbol.upper() != want_symbol:
                continue
            total_assets_raw = int(row[7])
            decimals = int(row[5] or 18)
            matches.append(
                {
                    "symbol": raw_symbol,
                    "chainId": chain_id,
                    "supplyRate": int(row[12]) / _RATE_PRECISION,
                    "borrowRate": None,
                    "totalSupplyUsd": None,
                    "totalBorrowUsd": None,
                    "tokenAddress": token_address,
                    "asset": str(row[6]).lower(),
                    "totalAssets": float(total_assets_raw) / float(10**decimals),
                    "vaultIndex": idx,
                }
            )
        if len(matches) == 1:
            return {"fToken": matches[0]}
        if not matches:
            raise ValueError("fluid: no matching onchain market in resolver payload")
        raise ValueError("fluid: ambiguous resolver match without unique vault or pool hint")

    def normalize_snapshot(
        self,
        market: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> OfficialMarketSnapshot:
        protocol = str(market.get("protocol", "fluid"))
        chain_id = int(market["chain_id"])
        ft = payload.get("fToken") or payload.get("resolver") or {}
        p_chain = ft.get("chainId")
        if p_chain is not None and int(p_chain) != chain_id:
            raise ValueError("fluid: chain_id mismatch in payload")

        supply = _to_float(_field_or_none(ft, "supplyRate", "supplyApy"))
        borrow = _to_float(_field_or_none(ft, "borrowRate", "borrowApy"))
        t_supply = _to_float(ft.get("totalSupplyUsd"))
        t_borrow = _to_float(ft.get("totalBorrowUsd"))
        meta = {"fToken": ft}
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
            matched_by="f_token",
            source_kind=self.source_kind,
            raw_metadata=meta,
        )


def _vault_index_hint(market: Mapping[str, Any]) -> int | None:
    raw_url = str(market.get("market_url") or "").strip()
    if not raw_url:
        return None
    parts = [part for part in urlparse(raw_url).path.split("/") if part]
    if "vaults" in parts:
        idx = parts.index("vaults")
        if idx + 2 < len(parts):
            try:
                return int(parts[idx + 2])
            except ValueError:
                return None
    return None
