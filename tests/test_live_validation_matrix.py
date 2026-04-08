"""Live validation matrix: protocol adapters, registry, and HTTP retry policy (TDD)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import httpx
import pytest

from validators.official_sources import get_protocol_source
from validators.browser_validation import (
    BROWSER_STATUS_BLOCKED,
    BROWSER_STATUS_FAIL,
    BrowserValidationOutcome,
)
from validators.protocol_adapters import (
    OfficialMarketSnapshot,
    UnknownProtocolAdapterError,
    build_protocol_adapter,
)
from validators.protocol_adapters.aave_v3 import AaveV3Adapter
from validators.protocol_api_compare import (
    compare_market_types,
    extract_markets_from_list_response,
    run_live_validation_matrix,
)
from validators.report_schema import build_live_validation_report, dump_report, load_report
from validators.protocol_adapters.euler_v2 import EulerV2Adapter
from validators.protocol_adapters.fluid import FluidAdapter
from validators.protocol_adapters.lista import ListaAdapter
from validators.protocol_adapters.morpho import MorphoAdapter
from validators.protocol_adapters.pendle import PendleAdapter
from validators.protocol_adapters.termmax import TermmaxAdapter
from validators.protocol_adapters.venus import VenusAdapter
from validators.retry_policy import (
    classify_http_failure,
    classify_retry_exhaustion,
    is_retryable_status,
    request_json_with_retry,
)


def _report_row(**kwargs: object):
    base: dict[str, object] = {
        "protocol": "aave_v3",
        "chain_id": 1,
        "market_id": "m1",
        "sample_rank": 1,
        "expected_url": "https://a.example/p",
        "resolved_url": "https://a.example/p",
        "browser_status": "NONE",
        "api_bucket": "PASS",
        "url_bucket": "PASS",
        "type_bucket": "PASS",
        "final_bucket": "PASS",
        "evidence_refs": ("ev:1",),
    }
    base.update(kwargs)
    if "evidence_refs" in kwargs and isinstance(kwargs["evidence_refs"], list):
        base["evidence_refs"] = tuple(kwargs["evidence_refs"])  # type: ignore[assignment]
    from validators.report_schema import LiveValidationRow

    return LiveValidationRow(**base)  # type: ignore[arg-type]


# --- Recorded-style minimal payloads (inline fixtures) ---

AAVE_V3_PAYLOAD = {
    "data": {
        "markets": [
            {
                "chain": {"chainId": 1},
                "reserves": [
                    {
                        "underlyingToken": {"symbol": "USDC"},
                        "liquidityAPY": {"raw": "0.031"},
                        "variableBorrowAPY": {"raw": "0.052"},
                        "size": {"usd": "2000000.5"},
                        "borrow": {"usd": "500000.25"},
                    }
                ],
            }
        ]
    }
}

MORPHO_PAYLOAD = {
    "data": {
        "markets": {
            "items": [
                {
                    "uniqueKey": "morpho-usdc-1",
                    "loanAsset": {"symbol": "USDC"},
                    "state": {
                        "avgSupplyApy": "0.028",
                        "avgBorrowApy": "0.045",
                        "supplyAssetsUsd": "1500000",
                        "borrowAssetsUsd": "600000",
                    },
                }
            ]
        }
    }
}

EULER_V2_PAYLOAD = {
    "data": {
        "vaults": [
            {
                "id": "0xvault1",
                "chainId": "1",
                "supplyAPY": "0.04",
                "borrowAPY": "0.065",
                "totalAssetsUsd": "3000000",
                "totalBorrowsUsd": "900000",
            }
        ]
    }
}

VENUS_PAYLOAD = {
    "result": [
        {
            "underlyingSymbol": "USDC",
            "chainId": "1",
            "supplyApy": "0.033",
            "borrowApy": "0.058",
            "totalSupplyUsd": "1200000",
            "totalBorrowUsd": "400000",
        }
    ]
}

FLUID_PAYLOAD = {
    "fToken": {
        "symbol": "fUSDC",
        "chainId": 1,
        "supplyRate": 0.029,
        "borrowRate": 0.049,
        "totalSupplyUsd": 8_000_000.0,
        "totalBorrowUsd": 2_500_000.0,
    }
}

TERMMAX_PAYLOAD = {
    "data": {
        "markets": [
            {
                "chainId": 1,
                "marketId": "tm-usdc",
                "supplyApy": 0.036,
                "borrowApy": 0.054,
                "tvlUsd": 1_100_000,
                "borrowedUsd": 300_000,
            }
        ]
    }
}

LISTA_PAYLOAD = {
    "cdp": {
        "collateralSymbol": "BNB",
        "chainId": 56,
        "stabilityFeeApy": 0.015,
        "totalCollateralUsd": 50_000_000,
        "totalDebtUsd": 18_000_000,
    }
}

PENDLE_PAYLOAD = {
    "data": [
        {
            "chainId": 1,
            "address": "0xpendle1",
            "impliedApy": 0.11,
            "liquidity": {"usd": 7_500_000},
            "pt": {"apy": 0.09},
            "yt": {"apy": 0.13},
        }
    ]
}


def _base_market(protocol: str, chain_id: int = 1) -> dict:
    return {
        "protocol": protocol,
        "chain_id": chain_id,
        "market_type": "primary",
        "expected_market_url": f"https://app.example/{protocol}",
        "symbol": "USDC",
    }


@pytest.mark.parametrize(
    ("protocol", "adapter_cls", "payload", "chain_override"),
    [
        ("aave_v3", AaveV3Adapter, AAVE_V3_PAYLOAD, 1),
        ("morpho", MorphoAdapter, MORPHO_PAYLOAD, 1),
        ("euler_v2", EulerV2Adapter, EULER_V2_PAYLOAD, 1),
        ("venus", VenusAdapter, VENUS_PAYLOAD, 1),
        ("fluid", FluidAdapter, FLUID_PAYLOAD, 1),
        ("termmax", TermmaxAdapter, TERMMAX_PAYLOAD, 1),
        ("lista", ListaAdapter, LISTA_PAYLOAD, 56),
        ("pendle", PendleAdapter, PENDLE_PAYLOAD, 1),
    ],
)
def test_normalize_recorded_payload_to_official_market_snapshot(
    protocol: str,
    adapter_cls: type,
    payload: dict,
    chain_override: int,
) -> None:
    market = _base_market(protocol, chain_id=chain_override)
    adapter = adapter_cls()
    snap = adapter.normalize_snapshot(market, payload)
    assert isinstance(snap, OfficialMarketSnapshot)
    assert snap.protocol == protocol
    assert snap.chain_id == chain_override
    assert snap.market_type == "primary"
    assert snap.expected_market_url == market["expected_market_url"]
    assert snap.matched_by
    assert snap.source_kind
    assert isinstance(snap.raw_metadata, dict)
    assert snap.supply_apy is not None or snap.implied_apy is not None


def test_build_protocol_adapter_returns_correct_adapter() -> None:
    assert isinstance(build_protocol_adapter("aave_v3"), AaveV3Adapter)
    assert isinstance(build_protocol_adapter("morpho"), MorphoAdapter)
    assert isinstance(build_protocol_adapter("euler_v2"), EulerV2Adapter)
    assert isinstance(build_protocol_adapter("venus"), VenusAdapter)
    assert isinstance(build_protocol_adapter("fluid"), FluidAdapter)
    assert isinstance(build_protocol_adapter("termmax"), TermmaxAdapter)
    assert isinstance(build_protocol_adapter("lista"), ListaAdapter)
    assert isinstance(build_protocol_adapter("pendle"), PendleAdapter)


def test_build_protocol_adapter_unknown_raises() -> None:
    with pytest.raises(UnknownProtocolAdapterError):
        build_protocol_adapter("not_a_real_protocol")


def test_aave_adapter_accepts_live_backend_market_shape() -> None:
    market = {
        "protocol": "aave_v3",
        "chain_id": 1,
        "chain_name": "Ethereum",
        "market_type": "pool",
        "market_url": "https://app.aave.com/pool",
        "debt_token": {"symbol": "USDC"},
    }
    snap = AaveV3Adapter().normalize_snapshot(market, AAVE_V3_PAYLOAD)
    assert snap.expected_market_url == "https://app.aave.com/pool"
    assert snap.supply_apy is not None


def test_aave_adapter_fetch_snapshot_live_transport() -> None:
    market = {
        "protocol": "aave_v3",
        "chain_id": 1,
        "market_type": "pool",
        "market_url": "https://app.aave.com/reserve-overview/?underlyingAsset=0x1&marketName=proto_mainnet_v3",
        "debt_token": {"symbol": "USDC"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        assert body["variables"] == {"chainIds": [1]}
        return httpx.Response(
            200,
            json={
                "data": {
                    "markets": [
                        {
                            "chain": {"chainId": 1},
                            "reserves": [
                                {
                                    "underlyingToken": {"symbol": "USDC", "address": "0x1"},
                                    "supplyInfo": {"apy": {"formatted": "3.1"}, "total": {"usd": "2000"}},
                                    "borrowInfo": {"apy": {"formatted": "5.2"}, "total": {"usd": "500"}},
                                }
                            ],
                        }
                    ]
                }
            },
        )

    adapter = AaveV3Adapter(transport=httpx.MockTransport(handler))
    payload = adapter.fetch_snapshot(market, get_protocol_source("aave_v3"))
    snap = adapter.normalize_snapshot(market, payload)
    assert snap.supply_apy == pytest.approx(0.031)
    assert snap.borrow_apy == pytest.approx(0.052)


def test_aave_adapter_uses_underlying_asset_hint_before_symbol() -> None:
    market = {
        "protocol": "aave_v3",
        "chain_id": 1,
        "market_type": "pool",
        "market_url": "https://app.aave.com/reserve-overview/?underlyingAsset=0xright&marketName=proto_mainnet_v3",
        "debt_token": {"symbol": "USDC"},
    }
    payload = {
        "data": {
            "markets": [
                {
                    "chain": {"chainId": 1},
                    "reserves": [
                        {
                            "underlyingToken": {"symbol": "USDC", "address": "0xwrong"},
                            "liquidityAPY": {"raw": "0.01"},
                            "variableBorrowAPY": {"raw": "0.02"},
                        },
                        {
                            "underlyingToken": {"symbol": "USDC", "address": "0xright"},
                            "liquidityAPY": {"raw": "0.03"},
                            "variableBorrowAPY": {"raw": "0.04"},
                        },
                    ],
                }
            ]
        }
    }
    snap = AaveV3Adapter().normalize_snapshot(market, payload)
    assert snap.supply_apy == pytest.approx(0.03)


def test_morpho_adapter_fetch_snapshot_uses_market_key_from_url() -> None:
    market = {
        "protocol": "morpho",
        "chain_id": 1,
        "market_type": "isolated",
        "market_url": "https://app.morpho.org/ethereum/market/target-key/usdc",
        "debt_token": {"symbol": "USDC"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        assert body["variables"] == {
            "first": 100,
            "skip": 0,
            "chainIds": [1],
            "uniqueKeys": ["target-key"],
        }
        return httpx.Response(
            200,
            json={
                "data": {
                    "markets": {
                        "items": [
                            {
                                "uniqueKey": "other-key",
                                "loanAsset": {"symbol": "USDC", "chain": {"id": 1}},
                                "state": {"avgSupplyApy": "0.01", "avgBorrowApy": "0.02", "supplyAssetsUsd": "1", "borrowAssetsUsd": "1"},
                            },
                            {
                                "uniqueKey": "target-key",
                                "loanAsset": {"symbol": "USDC", "chain": {"id": 1}},
                                "state": {"avgSupplyApy": "0.028", "avgBorrowApy": "0.045", "supplyAssetsUsd": "10", "borrowAssetsUsd": "5"},
                            },
                        ]
                    }
                }
            },
        )

    adapter = MorphoAdapter(transport=httpx.MockTransport(handler))
    payload = adapter.fetch_snapshot(market, get_protocol_source("morpho"))
    snap = adapter.normalize_snapshot(market, payload)
    assert snap.raw_metadata["market"]["uniqueKey"] == "target-key"


def test_morpho_adapter_rejects_ambiguous_symbol_without_unique_key() -> None:
    market = {
        "protocol": "morpho",
        "chain_id": 1,
        "market_type": "isolated",
        "debt_token": {"symbol": "USDC"},
    }
    payload = {
        "data": {
            "markets": {
                "items": [
                    {"uniqueKey": "a", "loanAsset": {"symbol": "USDC"}, "state": {}},
                    {"uniqueKey": "b", "loanAsset": {"symbol": "USDC"}, "state": {}},
                ]
            }
        }
    }
    with pytest.raises(ValueError, match="ambiguous"):
        MorphoAdapter().normalize_snapshot(market, payload)


def test_morpho_adapter_prefers_instantaneous_rates_over_avg_rates() -> None:
    market = {
        "protocol": "morpho",
        "chain_id": 1,
        "market_type": "isolated",
        "market_url": "https://app.morpho.org/ethereum/market/target-key/usdc",
        "debt_token": {"symbol": "USDC"},
    }
    payload = {
        "data": {
            "markets": {
                "items": [
                    {
                        "uniqueKey": "target-key",
                        "loanAsset": {"symbol": "USDC"},
                        "state": {
                            "supplyApy": "40.25",
                            "borrowApy": "40.40",
                            "avgSupplyApy": "5037.06",
                            "avgBorrowApy": "5074.62",
                        },
                    }
                ]
            }
        }
    }
    snap = MorphoAdapter().normalize_snapshot(market, payload)
    assert snap.supply_apy == pytest.approx(40.25)
    assert snap.borrow_apy == pytest.approx(40.40)


def test_euler_adapter_fetch_snapshot_uses_chain_subgraph_and_vault_url() -> None:
    market = {
        "protocol": "euler_v2",
        "chain_id": 1,
        "market_type": "isolated",
        "market_url": "https://app.euler.finance/vault/0xvault1?network=mainnet",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "euler-v2-mainnet" in str(request.url)
        return httpx.Response(
            200,
            json={
                "data": {
                    "eulerVaults": [
                        {"id": "0xother", "state": {"supplyApy": str(4 * 10**25), "borrowApy": str(6 * 10**25)}},
                        {"id": "0xvault1", "state": {"supplyApy": str(4 * 10**25), "borrowApy": str(6 * 10**25)}},
                    ]
                }
            },
        )

    adapter = EulerV2Adapter(transport=httpx.MockTransport(handler))
    payload = adapter.fetch_snapshot(market, get_protocol_source("euler_v2"))
    snap = adapter.normalize_snapshot(market, payload)
    assert snap.raw_metadata["vault"]["id"] == "0xvault1"
    assert snap.supply_apy == pytest.approx(0.04)


def test_euler_adapter_fetch_snapshot_uses_positions_url_vault_hint() -> None:
    market = {
        "protocol": "euler_v2",
        "chain_id": 1,
        "market_type": "isolated",
        "market_url": "https://app.euler.finance/positions/0xcollateral/0xvault1?network=ethereum",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "euler-v2-mainnet" in str(request.url)
        return httpx.Response(
            200,
            json={
                "data": {
                    "eulerVaults": [
                        {"id": "0xother", "state": {"supplyApy": str(1 * 10**25), "borrowApy": str(2 * 10**25)}},
                        {"id": "0xvault1", "state": {"supplyApy": str(4 * 10**25), "borrowApy": str(6 * 10**25)}},
                    ]
                }
            },
        )

    adapter = EulerV2Adapter(transport=httpx.MockTransport(handler))
    payload = adapter.fetch_snapshot(market, get_protocol_source("euler_v2"))
    snap = adapter.normalize_snapshot(market, payload)
    assert snap.raw_metadata["vault"]["id"] == "0xvault1"
    assert snap.borrow_apy == pytest.approx(0.06)


def test_venus_adapter_fetch_snapshot_core_pool_preserves_pool_address() -> None:
    market = {
        "protocol": "venus",
        "chain_id": 56,
        "market_type": "pool",
        "market_url": "https://venus.io/market#/markets/0xpool/0xmarket?chainId=56&tab=supply",
        "debt_token": {"symbol": "lisUSD"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/markets/core-pool")
        return httpx.Response(
            200,
            json={
                "result": [
                    {
                        "underlyingSymbol": "lisUSD",
                        "poolComptrollerAddress": "0xpool",
                        "supplyApyDecimal": "0.03",
                        "borrowApyDecimal": "0.05",
                        "totalSupplyUnderlyingCents": "120000",
                        "totalBorrowCents": "45000",
                    }
                ]
            },
        )

    adapter = VenusAdapter(transport=httpx.MockTransport(handler))
    payload = adapter.fetch_snapshot(market, get_protocol_source("venus"))
    snap = adapter.normalize_snapshot(market, payload)
    assert snap.total_supply_usd == pytest.approx(1200)
    assert snap.total_borrow_usd == pytest.approx(450)


def test_euler_adapter_rejects_ambiguous_chain_match_without_vault_id() -> None:
    market = {"protocol": "euler_v2", "chain_id": 1, "market_type": "isolated"}
    payload = {
        "data": {
            "vaults": [
                {"id": "0x1", "chainId": "1", "supplyAPY": 0.01, "borrowAPY": 0.02},
                {"id": "0x2", "chainId": "1", "supplyAPY": 0.03, "borrowAPY": 0.04},
            ]
        }
    }
    with pytest.raises(ValueError, match="ambiguous"):
        EulerV2Adapter().normalize_snapshot(market, payload)


def test_venus_adapter_fetch_snapshot_isolated_flattens_pool_markets() -> None:
    market = {
        "protocol": "venus",
        "chain_id": 1,
        "market_type": "isolated",
        "market_url": "https://venus.io/market/0xpool/0xvtoken?chainId=1",
        "debt_token": {"symbol": "USDC"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/pools")
        return httpx.Response(
            200,
            json={
                "result": [
                    {
                        "address": "0xpool",
                        "markets": [
                            {
                                "underlyingSymbol": "USDC",
                                "supplyApyDecimal": "0.03",
                                "borrowApyDecimal": "0.05",
                                "totalSupplyUnderlyingCents": "120000",
                                "totalBorrowCents": "45000",
                            }
                        ],
                    }
                ]
            },
        )

    adapter = VenusAdapter(transport=httpx.MockTransport(handler))
    payload = adapter.fetch_snapshot(market, get_protocol_source("venus"))
    snap = adapter.normalize_snapshot(market, payload)
    assert snap.total_supply_usd == pytest.approx(1200)
    assert snap.total_borrow_usd == pytest.approx(450)


def test_venus_adapter_rejects_ambiguous_symbol_without_pool_hint() -> None:
    market = {"protocol": "venus", "chain_id": 1, "market_type": "isolated", "debt_token": {"symbol": "USDC"}}
    payload = {
        "result": [
            {"underlyingSymbol": "USDC", "chainId": 1, "poolAddress": "0xa", "supplyApy": 0.03, "borrowApy": 0.05},
            {"underlyingSymbol": "USDC", "chainId": 1, "poolAddress": "0xb", "supplyApy": 0.04, "borrowApy": 0.06},
        ]
    }
    with pytest.raises(ValueError, match="ambiguous"):
        VenusAdapter().normalize_snapshot(market, payload)


def test_venus_adapter_reads_pool_hint_from_new_deep_link() -> None:
    market = {
        "protocol": "venus",
        "chain_id": 56,
        "market_type": "isolated",
        "market_url": "https://app.venus.io/pools/pool/0xpool/market/0xmarket",
        "debt_token": {"symbol": "USDC"},
    }
    payload = {
        "result": [
            {"underlyingSymbol": "USDC", "chainId": 56, "poolAddress": "0xpool", "supplyApy": 0.03, "borrowApy": 0.05},
        ]
    }
    snap = VenusAdapter().normalize_snapshot(market, payload)
    assert snap.supply_apy == pytest.approx(0.03)


def test_venus_adapter_reads_pool_hint_from_hash_route() -> None:
    market = {
        "protocol": "venus",
        "chain_id": 42161,
        "market_type": "isolated",
        "market_url": "https://venus.io/market#/markets/0xpool/0xmarket?chainId=42161&tab=supply",
        "debt_token": {"symbol": "WBTC"},
    }
    payload = {
        "result": [
            {"underlyingSymbol": "WBTC", "chainId": 42161, "poolAddress": "0xpool", "supplyApy": 0.03, "borrowApy": 0.05},
        ]
    }
    snap = VenusAdapter().normalize_snapshot(market, payload)
    assert snap.borrow_apy == pytest.approx(0.05)


def test_termmax_adapter_fetch_snapshot_matches_pool_from_market_url() -> None:
    market = {
        "protocol": "termmax",
        "chain_id": 42161,
        "market_type": "fixed_rate",
        "market_url": "https://app.termmax.ts.finance/market/arb/0xpool?chain=arb&persistChain=1",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/market/list")
        return httpx.Response(
            200,
            json={
                "data": {
                    "items": [
                        {
                            "entity": {
                                "address": "0xpool",
                                "chainId": 42161,
                                "stats": {
                                    "lend": {"apy": {"_val": "0.036"}},
                                    "borrow": {"apy": {"_val": "0.054"}},
                                    "lentValue": {"_val": "1100000"},
                                    "borrowedValue": {"_val": "300000"},
                                },
                            },
                            "pk": {"marketAddress": "0xpool"},
                        }
                    ]
                }
            },
        )

    adapter = TermmaxAdapter(transport=httpx.MockTransport(handler))
    payload = adapter.fetch_snapshot(market, get_protocol_source("termmax"))
    snap = adapter.normalize_snapshot(market, payload)
    assert snap.raw_metadata["market"]["poolAddress"] == "0xpool"


def test_termmax_adapter_rejects_ambiguous_chain_match_without_pool_hint() -> None:
    market = {"protocol": "termmax", "chain_id": 42161, "market_type": "fixed_rate"}
    payload = {
        "data": {
            "markets": [
                {"chainId": 42161, "marketId": "a", "poolAddress": "0xa", "supplyApy": 0.01, "borrowApy": 0.02},
                {"chainId": 42161, "marketId": "b", "poolAddress": "0xb", "supplyApy": 0.03, "borrowApy": 0.04},
            ]
        }
    }
    with pytest.raises(ValueError, match="ambiguous"):
        TermmaxAdapter().normalize_snapshot(market, payload)


def test_pendle_adapter_fetch_snapshot_reshapes_results_list() -> None:
    market = {
        "protocol": "pendle",
        "chain_id": 1,
        "market_type": "yield_split",
        "market_url": "https://app.pendle.finance/trade/markets/0xpendle1?chain=ethereum",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/1/markets")
        return httpx.Response(200, json={"results": PENDLE_PAYLOAD["data"]})

    adapter = PendleAdapter(transport=httpx.MockTransport(handler))
    payload = adapter.fetch_snapshot(market, get_protocol_source("pendle"))
    snap = adapter.normalize_snapshot(market, payload)
    assert snap.raw_metadata["market"]["address"] == "0xpendle1"


def test_pendle_adapter_rejects_ambiguous_chain_match_without_address_hint() -> None:
    market = {"protocol": "pendle", "chain_id": 1, "market_type": "yield_split"}
    payload = {
        "data": [
            {"chainId": 1, "address": "0xa", "pt": {"apy": 0.1}, "yt": {"apy": 0.2}},
            {"chainId": 1, "address": "0xb", "pt": {"apy": 0.3}, "yt": {"apy": 0.4}},
        ]
    }
    with pytest.raises(ValueError, match="ambiguous"):
        PendleAdapter().normalize_snapshot(market, payload)


def test_pendle_adapter_requires_market_address_hint_for_live_fetch() -> None:
    market = {"protocol": "pendle", "chain_id": 1, "market_type": "yield_split"}
    with pytest.raises(ValueError, match="market_url missing market address hint"):
        PendleAdapter(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": []}))).fetch_snapshot(
            market,
            get_protocol_source("pendle"),
        )


def test_fluid_adapter_fetch_snapshot_reads_onchain_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeCall:
        def __init__(self, result):
            self._result = result

        def call(self):
            return self._result

    class _FakeFunctions:
        def getFTokensEntireData(self):
            return _FakeCall(
                [
                    (
                        "0xf000",
                        False,
                        False,
                        "Fluid USDC",
                        "fUSDC",
                        6,
                        "0xa0",
                        12_000_000,
                        0,
                        0,
                        0,
                        200,
                        300,
                    )
                ]
            )

    class _FakeContract:
        functions = _FakeFunctions()

    class _FakeEth:
        def contract(self, address, abi):
            return _FakeContract()

    class _FakeWeb3:
        eth = _FakeEth()

        @staticmethod
        def to_checksum_address(value: str) -> str:
            return value

    monkeypatch.setattr("validators.protocol_adapters.fluid.make_web3", lambda chain_id: _FakeWeb3())
    market = {
        "protocol": "fluid",
        "chain_id": 1,
        "market_type": "pool",
        "market_url": "https://fluid.io/vaults/1/1",
        "debt_token": {"symbol": "USDC"},
    }
    adapter = FluidAdapter()
    payload = adapter.fetch_snapshot(market, get_protocol_source("fluid"))
    snap = adapter.normalize_snapshot(market, payload)
    assert snap.raw_metadata["fToken"]["tokenAddress"] == "0xf000"


def test_fluid_adapter_rejects_ambiguous_match_without_unique_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeCall:
        def __init__(self, result):
            self._result = result

        def call(self):
            return self._result

    class _FakeFunctions:
        def getFTokensEntireData(self):
            return _FakeCall(
                [
                    ("0xf001", False, False, "Fluid USDC A", "fUSDC", 6, "0xa0", 12_000_000, 0, 0, 0, 200, 300),
                    ("0xf002", False, False, "Fluid USDC B", "fUSDC", 6, "0xa1", 15_000_000, 0, 0, 0, 220, 320),
                ]
            )

    class _FakeContract:
        functions = _FakeFunctions()

    class _FakeEth:
        def contract(self, address, abi):
            return _FakeContract()

    class _FakeWeb3:
        eth = _FakeEth()

        @staticmethod
        def to_checksum_address(value: str) -> str:
            return value

    monkeypatch.setattr("validators.protocol_adapters.fluid.make_web3", lambda chain_id: _FakeWeb3())
    market = {"protocol": "fluid", "chain_id": 1, "market_type": "pool", "debt_token": {"symbol": "USDC"}}
    with pytest.raises(ValueError, match="ambiguous"):
        FluidAdapter().fetch_snapshot(market, get_protocol_source("fluid"))


def test_fluid_adapter_preserves_zero_supply_rate() -> None:
    market = {
        "protocol": "fluid",
        "chain_id": 42161,
        "market_type": "pool",
        "market_url": "https://fluid.io/vaults/42161/5",
    }
    snap = FluidAdapter().normalize_snapshot(
        market,
        {
            "fToken": {
                "chainId": 42161,
                "symbol": "fweETH",
                "supplyRate": 0.0,
                "borrowRate": None,
            }
        },
    )
    assert snap.supply_apy == 0.0


def test_lista_adapter_fetch_snapshot_reads_onchain_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeCall:
        def __init__(self, result):
            self._result = result

        def call(self):
            return self._result

    class _FakeJugFunctions:
        def ilks(self, _ilk):
            return _FakeCall((10**27, 0))

    class _FakeVatFunctions:
        def ilks(self, _ilk):
            return _FakeCall((10**18, 10**27, 0, 0, 0))

    class _FakeBalanceFunctions:
        def balanceOf(self, _address):
            return _FakeCall(5 * 10**18)

    class _FakeContract:
        def __init__(self, functions):
            self.functions = functions

    class _FakeEth:
        def contract(self, address, abi):
            if abi and abi[0]["name"] == "ilks" and len(abi[0]["outputs"]) == 2:
                return _FakeContract(_FakeJugFunctions())
            if abi and abi[0]["name"] == "ilks":
                return _FakeContract(_FakeVatFunctions())
            return _FakeContract(_FakeBalanceFunctions())

    class _FakeWeb3:
        eth = _FakeEth()

        @staticmethod
        def to_checksum_address(value: str) -> str:
            return value

    monkeypatch.setattr("validators.protocol_adapters.lista.make_web3", lambda chain_id: _FakeWeb3())
    market = {
        "protocol": "lista",
        "chain_id": 56,
        "market_type": "isolated",
        "collateral_token": {"symbol": "BTCB"},
    }
    adapter = ListaAdapter()
    payload = adapter.fetch_snapshot(market, get_protocol_source("lista"))
    snap = adapter.normalize_snapshot(market, payload)
    assert snap.raw_metadata["cdp"]["collateralSymbol"] == "BTCB"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, "retryable"),
        (503, "retryable"),
        (502, "retryable"),
        (500, "retryable"),
        (504, "retryable"),
        (403, "blocked"),
        (451, "blocked"),
        (404, "hard_fail"),
        (400, "hard_fail"),
        (422, "hard_fail"),
    ],
)
def test_classify_http_failure_status_codes(status: int, expected: str) -> None:
    assert classify_http_failure(status_code=status) == expected


def test_classify_http_failure_timeout_exception_retryable() -> None:
    exc = httpx.TimeoutException("timed out")
    assert classify_http_failure(exc=exc) == "retryable"


def test_is_retryable_status() -> None:
    assert is_retryable_status(429) is True
    assert is_retryable_status(503) is True
    assert is_retryable_status(403) is False
    assert is_retryable_status(404) is False


def test_classify_retry_exhaustion_marks_blocked() -> None:
    assert classify_retry_exhaustion("retryable", attempts_used=3, max_attempts=3) == "blocked"
    assert classify_retry_exhaustion("retryable", attempts_used=2, max_attempts=3) == "retryable"
    assert classify_retry_exhaustion("hard_fail", attempts_used=3, max_attempts=3) == "hard_fail"


def test_request_json_with_retry_retries_on_503_then_succeeds() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": "upstream"})
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        with mock.patch("validators.retry_policy.time.sleep", lambda *_: None):
            resp = request_json_with_retry(
                client,
                "GET",
                "https://example.test/x",
                max_attempts=4,
                backoff_base_s=0.0,
            )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert len(calls) == 2


def test_request_json_with_retry_raises_after_retryable_exhaustion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "still bad"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        with mock.patch("validators.retry_policy.time.sleep", lambda *_: None):
            with pytest.raises(httpx.HTTPStatusError):
                request_json_with_retry(
                    client,
                    "GET",
                    "https://example.test/y",
                    max_attempts=2,
                    backoff_base_s=0.0,
                )


def test_request_json_with_retry_retries_on_remote_protocol_error() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            raise httpx.RemoteProtocolError("boom")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        with mock.patch("validators.retry_policy.time.sleep", lambda *_: None):
            resp = request_json_with_retry(
                client,
                "GET",
                "https://example.test/z",
                max_attempts=3,
                backoff_base_s=0.0,
            )
    assert resp.status_code == 200
    assert calls["count"] == 2


def _aligned_aave_backend_market(market_id: str = "usdc-1") -> dict:
    m = {
        "protocol": "aave_v3",
        "chain_id": 1,
        "chain_name": "Ethereum",
        "market_type": "pool",
        "expected_market_url": "https://app.aave.com/pool",
        "market_url": "https://app.aave.com/pool",
        "market_id": market_id,
        "debt_token": {
            "symbol": "USDC",
            "address": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        },
    }
    m.pop("expected_market_url")
    snap = AaveV3Adapter().normalize_snapshot(m, AAVE_V3_PAYLOAD)
    return {
        **m,
        "supply_apy": snap.supply_apy,
        "borrow_apy": snap.borrow_apy,
        "net_supply_apy": snap.net_supply_apy,
        "net_borrow_apy": snap.net_borrow_apy,
    }


class _FakeListMarketsClient:
    def __init__(self, by_protocol: dict[str, list[dict]]) -> None:
        self.by_protocol = by_protocol

    def list_protocols(self) -> dict:
        data = []
        for protocol, rows in self.by_protocol.items():
            chains = sorted({int(row.get("chain_id") or 0) for row in rows})
            data.append({"id": protocol, "chains": chains})
        return {"data": data}

    def list_markets(
        self,
        *,
        protocol: str | None = None,
        chain_id: int | None = None,
        limit: int | None = None,
        is_expired: bool | None = None,
        is_disabled: bool | None = None,
    ) -> dict:
        assert is_expired is False
        assert is_disabled is False
        rows = list(self.by_protocol.get(protocol or "", []))
        if chain_id is not None:
            rows = [row for row in rows if int(row.get("chain_id") or 0) == chain_id]
        if limit is not None:
            rows = rows[:limit]
        return {"data": rows}


def test_extract_markets_from_list_response_data_and_markets_keys() -> None:
    m = [{"market_id": "a"}]
    assert extract_markets_from_list_response({"data": m}) == m
    assert extract_markets_from_list_response({"markets": m}) == m
    assert extract_markets_from_list_response({}) == []


def test_compare_market_types_pass_and_bug() -> None:
    assert compare_market_types({"market_type": "primary"}, {"market_type": "primary"})["bucket"] == "PASS"
    assert compare_market_types({"market_type": "secondary"}, {"market_type": "primary"})["bucket"] == "BUG"


def test_run_live_validation_matrix_caps_at_five_per_chain() -> None:
    markets = [_aligned_aave_backend_market(f"id-{i}") for i in range(10)]
    client = _FakeListMarketsClient({"aave_v3": markets})

    def provider(protocol: str, market: dict) -> dict:
        assert protocol == "aave_v3"
        return AAVE_V3_PAYLOAD

    rep = run_live_validation_matrix(
        client,
        run_id="t1",
        ruleset_version="1.0.0",
        protocols=["aave_v3"],
        per_chain_limit=5,
        official_payload_provider=provider,
    )
    assert len(rep.rows) == 5
    by_chain: dict[int, int] = {}
    for r in rep.rows:
        by_chain[r.chain_id] = by_chain.get(r.chain_id, 0) + 1
    assert by_chain.get(1, 0) == 5


def test_run_live_validation_matrix_fetches_markets_per_supported_chain() -> None:
    eth = _aligned_aave_backend_market("eth-1")
    arb = dict(_aligned_aave_backend_market("arb-1"))
    arb["chain_id"] = 42161
    arb["chain_name"] = "Arbitrum"
    arb["market_id"] = "arb-1"
    client = _FakeListMarketsClient({"aave_v3": [eth, arb]})

    rep = run_live_validation_matrix(
        client,
        run_id="chain-coverage",
        ruleset_version="1.0.0",
        protocols=["aave_v3"],
        per_chain_limit=5,
        official_payload_provider=lambda protocol, market: AAVE_V3_PAYLOAD,
    )
    assert {row.chain_id for row in rep.rows} == {1, 42161}


def test_run_live_validation_matrix_uses_live_fetch_when_provider_absent() -> None:
    market = {
        "protocol": "aave_v3",
        "chain_id": 1,
        "market_type": "pool",
        "market_url": "https://app.aave.com/reserve-overview/?underlyingAsset=0x1&marketName=proto_mainnet_v3",
        "market_id": "live-aave",
        "debt_token": {"symbol": "USDC"},
        "supply_apy": 0.031,
        "borrow_apy": 0.052,
    }
    client = _FakeListMarketsClient({"aave_v3": [market]})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "markets": [
                        {
                            "chain": {"chainId": 1},
                            "reserves": [
                                {
                                    "underlyingToken": {"symbol": "USDC", "address": "0x1"},
                                    "supplyInfo": {"apy": {"formatted": "3.1"}, "total": {"usd": "2000"}},
                                    "borrowInfo": {"apy": {"formatted": "5.2"}, "total": {"usd": "500"}},
                                }
                            ],
                        }
                    ]
                }
            },
        )

    rep = run_live_validation_matrix(
        client,
        run_id="live-fetch",
        ruleset_version="1.0.0",
        protocols=["aave_v3"],
        official_data_mode="live",
        browser_runtime_mode="off",
        official_payload_provider=None,
        adapter_transport=httpx.MockTransport(handler),
    )
    assert rep.official_data_mode == "live"
    assert len(rep.rows) == 1
    assert rep.rows[0].final_bucket == "PASS"


def test_run_live_validation_matrix_turns_live_fetch_error_into_fail_row() -> None:
    market = {
        "protocol": "aave_v3",
        "chain_id": 1,
        "market_type": "pool",
        "market_url": "https://app.aave.com/reserve-overview/?underlyingAsset=0x1&marketName=proto_mainnet_v3",
        "market_id": "broken-aave",
    }
    client = _FakeListMarketsClient({"aave_v3": [market]})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "upstream down"})

    rep = run_live_validation_matrix(
        client,
        run_id="live-fetch-error",
        ruleset_version="1.0.0",
        protocols=["aave_v3"],
        official_data_mode="live",
        official_payload_provider=None,
        adapter_transport=httpx.MockTransport(handler),
    )
    assert len(rep.rows) == 1
    assert rep.rows[0].final_bucket == "FAIL"


def test_run_live_validation_matrix_empty_markets_yields_no_rows() -> None:
    client = _FakeListMarketsClient({"aave_v3": []})
    rep = run_live_validation_matrix(
        client,
        run_id="empty",
        ruleset_version="1.0.0",
        protocols=["aave_v3"],
        official_payload_provider=lambda p, m: AAVE_V3_PAYLOAD,
    )
    assert rep.rows == ()
    assert rep.matrix_status == "PASS"


def test_run_live_validation_matrix_unknown_protocol_produces_no_rows() -> None:
    client = _FakeListMarketsClient({})
    rep = run_live_validation_matrix(
        client,
        run_id="t2",
        ruleset_version="1.0.0",
        protocols=["not_a_registered_protocol"],
        official_payload_provider=lambda p, m: {},
    )
    assert rep.rows == ()
    assert rep.matrix_status == "PASS"


def test_run_live_validation_matrix_aggregate_fail_on_metric_mismatch() -> None:
    m = _aligned_aave_backend_market()
    m["supply_apy"] = 99.0
    client = _FakeListMarketsClient({"aave_v3": [m]})

    rep = run_live_validation_matrix(
        client,
        run_id="t3",
        ruleset_version="1.0.0",
        protocols=["aave_v3"],
        official_payload_provider=lambda p, mm: AAVE_V3_PAYLOAD,
    )
    assert rep.matrix_status == "FAIL"
    assert rep.rows[0].final_bucket == "FAIL"


def test_run_live_validation_matrix_browser_fail_yields_fail() -> None:
    m = _aligned_aave_backend_market()

    class _FailBrowser:
        def validate_market_url(self, market: dict) -> BrowserValidationOutcome:
            return BrowserValidationOutcome(
                resolved_url="https://app.aave.com/pool",
                status=BROWSER_STATUS_FAIL,
                summary="markers",
                metadata={},
            )

    client = _FakeListMarketsClient({"aave_v3": [m]})
    rep = run_live_validation_matrix(
        client,
        run_id="t4b",
        ruleset_version="1.0.0",
        protocols=["aave_v3"],
        official_payload_provider=lambda p, mm: AAVE_V3_PAYLOAD,
        browser_adapter=_FailBrowser(),
    )
    assert rep.matrix_status == "FAIL"
    assert rep.rows[0].final_bucket == "FAIL"


def test_run_live_validation_matrix_blocked_overrides_fail() -> None:
    m = _aligned_aave_backend_market()

    class _Blocked:
        def validate_market_url(self, market: dict) -> BrowserValidationOutcome:
            return BrowserValidationOutcome(
                resolved_url="https://blocked.example/",
                status=BROWSER_STATUS_BLOCKED,
                summary="geo",
                metadata={},
            )

    client = _FakeListMarketsClient({"aave_v3": [m]})
    rep = run_live_validation_matrix(
        client,
        run_id="t4",
        ruleset_version="1.0.0",
        protocols=["aave_v3"],
        official_payload_provider=lambda p, mm: AAVE_V3_PAYLOAD,
        browser_adapter=_Blocked(),
    )
    assert rep.matrix_status == "BLOCKED"
    assert rep.rows[0].final_bucket == "BLOCKED"


def test_run_live_validation_matrix_closes_browser_adapter() -> None:
    m = _aligned_aave_backend_market()
    closed = {"value": False}

    class _ClosableBrowser:
        def validate_market_url(self, market: dict) -> BrowserValidationOutcome:
            return BrowserValidationOutcome(
                resolved_url=str(market["market_url"]),
                status="PASS",
                summary="ok",
                metadata={},
            )

        def close(self) -> None:
            closed["value"] = True

    client = _FakeListMarketsClient({"aave_v3": [m]})
    rep = run_live_validation_matrix(
        client,
        run_id="t4c",
        ruleset_version="1.0.0",
        protocols=["aave_v3"],
        official_payload_provider=lambda p, mm: AAVE_V3_PAYLOAD,
        browser_adapter=_ClosableBrowser(),
    )
    assert rep.matrix_status == "PASS"
    assert closed["value"] is True


def test_run_live_validation_matrix_dry_run_matrix_status_non_pushable() -> None:
    m = _aligned_aave_backend_market()
    client = _FakeListMarketsClient({"aave_v3": [m]})
    rep = run_live_validation_matrix(
        client,
        run_id="t5",
        ruleset_version="1.0.0",
        protocols=["aave_v3"],
        official_payload_provider=lambda p, mm: AAVE_V3_PAYLOAD,
        dry_run=True,
    )
    assert rep.matrix_status == "DRY_RUN_BLOCKED"
    assert rep.rows[0].final_bucket == "PASS"


def test_run_live_validation_matrix_report_roundtrip(tmp_path) -> None:
    m = _aligned_aave_backend_market()
    client = _FakeListMarketsClient({"aave_v3": [m]})
    rep = run_live_validation_matrix(
        client,
        run_id="t6",
        ruleset_version="2.2.2",
        protocols=["aave_v3"],
        official_payload_provider=lambda p, mm: AAVE_V3_PAYLOAD,
    )
    path = tmp_path / "lv.json"
    dump_report(rep, path)
    again = load_report(path)
    assert again.run_id == rep.run_id
    assert len(again.rows) == 1


def test_snapshot_to_dict_roundtrip_keys() -> None:
    adapter = AaveV3Adapter()
    snap = adapter.normalize_snapshot(_base_market("aave_v3"), AAVE_V3_PAYLOAD)
    d = snap.as_dict()
    for key in (
        "protocol",
        "chain_id",
        "market_type",
        "expected_market_url",
        "supply_apy",
        "borrow_apy",
        "net_supply_apy",
        "net_borrow_apy",
        "implied_apy",
        "total_supply_usd",
        "total_borrow_usd",
        "matched_by",
        "source_kind",
        "raw_metadata",
    ):
        assert key in d
    json.dumps(d)  # serializable


_REPO_ROOT = Path(__file__).resolve().parents[1]
_GATE_SCRIPT = _REPO_ROOT / "scripts" / "check_backend_push_gate.py"


def _run_push_gate(report_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_GATE_SCRIPT), str(report_path)],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )


def test_push_gate_pass_report_exits_zero(tmp_path: Path) -> None:
    rep = build_live_validation_report(
        run_id="gate-pass",
        ruleset_version="1.0.0",
        rows=[_report_row(browser_status="PASS")],
        official_data_mode="live",
        browser_runtime_mode="real",
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        dry_run=False,
    )
    path = tmp_path / "ok.json"
    dump_report(rep, path)
    proc = _run_push_gate(path)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "gate: OK" in proc.stdout


def test_push_gate_fail_report_exits_nonzero(tmp_path: Path) -> None:
    rep = build_live_validation_report(
        run_id="gate-fail",
        ruleset_version="1.0.0",
        rows=[
            _report_row(
                final_bucket="FAIL",
                browser_status="PASS",
                api_bucket="BUG",
                url_bucket="PASS",
                type_bucket="PASS",
            ),
        ],
        official_data_mode="live",
        browser_runtime_mode="real",
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        dry_run=False,
    )
    path = tmp_path / "fail.json"
    dump_report(rep, path)
    proc = _run_push_gate(path)
    assert proc.returncode != 0
    assert "matrix_status" in proc.stderr or "PASS" in proc.stderr


def test_push_gate_blocked_report_exits_nonzero(tmp_path: Path) -> None:
    rep = build_live_validation_report(
        run_id="gate-blocked",
        ruleset_version="1.0.0",
        rows=[
            _report_row(
                final_bucket="BLOCKED",
                browser_status="BLOCKED",
                api_bucket="PASS",
                url_bucket="PASS",
                type_bucket="PASS",
            ),
        ],
        official_data_mode="live",
        browser_runtime_mode="real",
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        dry_run=False,
    )
    path = tmp_path / "blocked.json"
    dump_report(rep, path)
    proc = _run_push_gate(path)
    assert proc.returncode != 0


def test_push_gate_dry_run_exits_nonzero(tmp_path: Path) -> None:
    rep = build_live_validation_report(
        run_id="gate-dry",
        ruleset_version="1.0.0",
        rows=[_report_row(browser_status="PASS")],
        official_data_mode="live",
        browser_runtime_mode="real",
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        dry_run=True,
    )
    path = tmp_path / "dry.json"
    dump_report(rep, path)
    proc = _run_push_gate(path)
    assert proc.returncode != 0
    assert "dry_run" in proc.stderr.lower()


def test_push_gate_rejects_recorded_or_stub_modes(tmp_path: Path) -> None:
    rep = build_live_validation_report(
        run_id="gate-recorded",
        ruleset_version="1.0.0",
        rows=[_report_row(browser_status="STUB")],
        official_data_mode="recorded",
        browser_runtime_mode="stub",
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        dry_run=False,
    )
    path = tmp_path / "recorded.json"
    dump_report(rep, path)
    proc = _run_push_gate(path)
    assert proc.returncode != 0
    assert "official_data_mode" in proc.stderr or "browser_runtime_mode" in proc.stderr


def test_push_gate_rejects_blocked_with_distinct_exit_code(tmp_path: Path) -> None:
    rep = build_live_validation_report(
        run_id="gate-blocked-exit",
        ruleset_version="1.0.0",
        rows=[
            _report_row(
                final_bucket="BLOCKED",
                browser_status="BLOCKED",
                api_bucket="PASS",
                url_bucket="PASS",
                type_bucket="PASS",
            ),
        ],
        official_data_mode="live",
        browser_runtime_mode="real",
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        dry_run=False,
    )
    path = tmp_path / "blocked-exit.json"
    dump_report(rep, path)
    proc = _run_push_gate(path)
    assert proc.returncode == 3


def test_push_gate_rejects_empty_live_report(tmp_path: Path) -> None:
    rep = build_live_validation_report(
        run_id="gate-empty",
        ruleset_version="1.0.0",
        rows=[],
        official_data_mode="live",
        browser_runtime_mode="real",
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        dry_run=False,
    )
    path = tmp_path / "empty.json"
    dump_report(rep, path)
    proc = _run_push_gate(path)
    assert proc.returncode == 4
    assert "empty matrix report" in proc.stderr.lower()


def test_matrix_cli_uses_live_mode_without_recorded_payloads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import scripts.run_live_validation_matrix as cli

    calls: dict[str, object] = {}

    class _FakeBackendClient:
        def __init__(self, base_url: str, *, headers=None):
            calls["base_url"] = base_url
            calls["headers"] = headers

        def close(self) -> None:
            calls["closed"] = True

    def fake_run_live_validation_matrix(client, **kwargs):
        calls["kwargs"] = kwargs
        return build_live_validation_report(
            run_id="cli-live",
            ruleset_version="1.0.0",
            rows=[],
            official_data_mode=str(kwargs["official_data_mode"]),
            browser_runtime_mode=str(kwargs["browser_runtime_mode"]),
            dry_run=False,
        )

    monkeypatch.setenv("OMRATE_BACKEND_BASE_URL", "http://backend.test")
    monkeypatch.setattr(cli, "BackendValidationClient", _FakeBackendClient)
    monkeypatch.setattr(cli, "run_live_validation_matrix", fake_run_live_validation_matrix)

    exit_code = cli.main(["--output", str(tmp_path / "cli-test.json")])
    assert exit_code == 4
    assert calls["kwargs"]["official_data_mode"] == "live"
    assert calls["kwargs"]["official_payload_provider"] is None


def test_matrix_cli_real_browser_mode_wires_playwright_adapter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import scripts.run_live_validation_matrix as cli

    calls: dict[str, object] = {}

    class _FakeBackendClient:
        def __init__(self, base_url: str, *, headers=None):
            calls["base_url"] = base_url

        def close(self) -> None:
            calls["closed"] = True

    class _FakeInspector:
        def __init__(self, **kwargs):
            calls["inspector_kwargs"] = kwargs

    class _FakeRealAdapter:
        def __init__(self, inspector):
            calls["inspector"] = inspector

    def fake_run_live_validation_matrix(client, **kwargs):
        calls["kwargs"] = kwargs
        return build_live_validation_report(
            run_id="cli-real",
            ruleset_version="1.0.0",
            rows=[],
            official_data_mode="live",
            browser_runtime_mode="real",
            dry_run=False,
        )

    monkeypatch.setenv("OMRATE_BACKEND_BASE_URL", "http://backend.test")
    monkeypatch.setenv("PLAYWRIGHT_TIMEOUT_MS", "1234")
    monkeypatch.setenv("PLAYWRIGHT_WAIT_AFTER_LOAD_MS", "567")
    monkeypatch.setenv("PLAYWRIGHT_HEADLESS", "0")
    monkeypatch.setattr(cli, "BackendValidationClient", _FakeBackendClient)
    monkeypatch.setattr(cli, "PlaywrightBrowserInspector", _FakeInspector)
    monkeypatch.setattr(cli, "RealBrowserValidationAdapter", _FakeRealAdapter)
    monkeypatch.setattr(cli, "run_live_validation_matrix", fake_run_live_validation_matrix)

    exit_code = cli.main(["--browser-mode", "real", "--output", str(tmp_path / "cli-real.json")])
    assert exit_code == 0
    assert calls["kwargs"]["browser_runtime_mode"] == "real"
    assert isinstance(calls["inspector"], _FakeInspector)
    assert calls["inspector_kwargs"]["headless"] is False
    assert calls["inspector_kwargs"]["timeout_ms"] == 1234
    assert calls["inspector_kwargs"]["wait_after_load_ms"] == 567
