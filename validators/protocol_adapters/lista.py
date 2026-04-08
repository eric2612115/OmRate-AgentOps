"""Lista CDP / on-chain style payload normalization."""

from __future__ import annotations

from typing import Any, Mapping

from validators.official_sources import ProtocolOfficialSource
from validators.protocol_adapters import OfficialMarketSnapshot, market_expected_url
from validators.protocol_adapters.live_fetch import make_web3

_JUG_ABI = [
    {
        "inputs": [{"internalType": "bytes32", "name": "ilk", "type": "bytes32"}],
        "name": "ilks",
        "outputs": [
            {"internalType": "uint256", "name": "duty", "type": "uint256"},
            {"internalType": "uint256", "name": "rho", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]
_VAT_ABI = [
    {
        "inputs": [{"internalType": "bytes32", "name": "ilk", "type": "bytes32"}],
        "name": "ilks",
        "outputs": [
            {"internalType": "uint256", "name": "Art", "type": "uint256"},
            {"internalType": "uint256", "name": "rate", "type": "uint256"},
            {"internalType": "uint256", "name": "spot", "type": "uint256"},
            {"internalType": "uint256", "name": "line", "type": "uint256"},
            {"internalType": "uint256", "name": "dust", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]
_ERC20_BALANCE_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]
_RAY = 10**27
_SECONDS_PER_YEAR = 365 * 24 * 3600


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


class ListaAdapter:
    protocol_id = "lista"
    source_kind = "onchain_cdp"

    def __init__(self, transport: Any = None) -> None:
        self._transport = transport

    def fetch_snapshot(
        self,
        market: Mapping[str, Any],
        source: ProtocolOfficialSource,
    ) -> Mapping[str, Any]:
        runtime = source.runtime_metadata or {}
        chain_id = int(runtime.get("chain_id") or market["chain_id"])
        contracts = runtime.get("contracts") or {}
        collaterals = runtime.get("collaterals") or []
        want_address = str(((market.get("collateral_token") or {}).get("address") or "")).strip().lower()
        want_symbol = str(((market.get("collateral_token") or {}).get("symbol") or "")).strip().upper()
        matched = None
        for collateral in collaterals:
            addr = str(collateral.get("address") or "").strip().lower()
            symbol = str(collateral.get("symbol") or "").strip().upper()
            if want_address and addr == want_address:
                matched = collateral
                break
            if want_symbol and symbol == want_symbol:
                matched = collateral
                break
        if matched is None:
            raise ValueError("lista: no collateral metadata match for market")

        w3 = make_web3(chain_id)
        jug = w3.eth.contract(
            address=w3.to_checksum_address(str(contracts["jug"])),
            abi=_JUG_ABI,
        )
        vat = w3.eth.contract(
            address=w3.to_checksum_address(str(contracts["vat"])),
            abi=_VAT_ABI,
        )
        collateral_token = w3.eth.contract(
            address=w3.to_checksum_address(str(matched["address"])),
            abi=_ERC20_BALANCE_ABI,
        )
        ilk = bytes.fromhex(str(matched["ilk_hex"]))
        duty, _rho = jug.functions.ilks(ilk).call()
        art, rate, _spot, _line, _dust = vat.functions.ilks(ilk).call()
        total_collateral_raw = collateral_token.functions.balanceOf(
            w3.to_checksum_address(str(matched["gem_join"]))
        ).call()
        decimals = int(matched.get("decimals") or 18)
        borrow_apy = (int(duty) / _RAY) ** _SECONDS_PER_YEAR - 1
        total_debt = int(art) * int(rate) / _RAY / float(10 ** int((runtime.get("lisusd") or {}).get("decimals") or 18))
        total_collateral = float(int(total_collateral_raw)) / float(10**decimals)
        return {
            "cdp": {
                "collateralSymbol": matched.get("symbol"),
                "chainId": chain_id,
                "stabilityFeeApy": borrow_apy,
                "totalCollateralUsd": None,
                "totalDebtUsd": total_debt,
                "totalCollateralTokenAmount": total_collateral,
                "gemJoin": matched.get("gem_join"),
            }
        }

    def normalize_snapshot(
        self,
        market: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> OfficialMarketSnapshot:
        protocol = str(market.get("protocol", "lista"))
        chain_id = int(market["chain_id"])
        cdp = payload.get("cdp") or payload.get("vault") or {}
        p_chain = cdp.get("chainId")
        if p_chain is not None and int(p_chain) != chain_id:
            raise ValueError("lista: chain_id mismatch in payload")

        fee = _to_float(cdp.get("stabilityFeeApy"))
        t_col = _to_float(cdp.get("totalCollateralUsd"))
        t_debt = _to_float(cdp.get("totalDebtUsd"))
        meta = {"cdp": cdp}
        return OfficialMarketSnapshot(
            protocol=protocol,
            chain_id=chain_id,
            market_type=str(market.get("market_type", "unknown")),
            expected_market_url=market_expected_url(market),
            supply_apy=None,
            borrow_apy=fee,
            net_supply_apy=None,
            net_borrow_apy=fee,
            implied_apy=fee,
            total_supply_usd=t_col,
            total_borrow_usd=t_debt,
            matched_by="cdp_aggregate",
            source_kind=self.source_kind,
            raw_metadata=meta,
        )
