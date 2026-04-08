"""Tests for deterministic per-chain market sampling (TDD)."""

from __future__ import annotations

from validators.sampling import group_markets_by_chain, sample_markets_by_chain


def _m(
    mid: str,
    *,
    chain_name: str | None = "ethereum",
    chain_id: int | str = 1,
    is_expired: bool = False,
    is_disabled: bool = False,
) -> dict:
    return {
        "market_id": mid,
        "protocol": "aave_v3",
        "chain_id": chain_id,
        "chain_name": chain_name,
        "is_expired": is_expired,
        "is_disabled": is_disabled,
    }


def test_single_chain_more_than_five_takes_five_deterministically() -> None:
    markets = [_m(f"m{i}") for i in range(6)]
    out = sample_markets_by_chain(markets, per_chain_limit=5)
    assert len(out) == 5
    ids = [m["market_id"] for m in out]
    assert ids == ["m0", "m1", "m2", "m3", "m4"]


def test_multiple_chains_each_capped_at_five() -> None:
    eth = [_m(f"e{i}", chain_name="ethereum", chain_id=1) for i in range(7)]
    pol = [_m(f"p{i}", chain_name="polygon", chain_id=137) for i in range(6)]
    markets = eth + pol
    out = sample_markets_by_chain(markets, per_chain_limit=5)
    assert len(out) == 10
    eth_ids = sorted(m["market_id"] for m in out if m["market_id"].startswith("e"))
    pol_ids = sorted(m["market_id"] for m in out if m["market_id"].startswith("p"))
    assert eth_ids == ["e0", "e1", "e2", "e3", "e4"]
    assert pol_ids == ["p0", "p1", "p2", "p3", "p4"]


def test_fewer_than_limit_takes_all() -> None:
    markets = [_m("a"), _m("b")]
    out = sample_markets_by_chain(markets, per_chain_limit=5)
    assert [m["market_id"] for m in out] == ["a", "b"]


def test_expired_and_disabled_excluded() -> None:
    markets = [
        _m("ok1"),
        _m("bad_exp", is_expired=True),
        _m("ok2"),
        _m("bad_dis", is_disabled=True),
    ]
    out = sample_markets_by_chain(markets, per_chain_limit=5)
    assert [m["market_id"] for m in out] == ["ok1", "ok2"]


def test_same_input_produces_identical_sample_multiple_times() -> None:
    markets = [_m(f"x{i}") for i in range(12)] + [
        _m(f"y{i}", chain_name="base", chain_id=8453) for i in range(8)
    ]
    a = sample_markets_by_chain(markets, per_chain_limit=5)
    b = sample_markets_by_chain(markets, per_chain_limit=5)
    assert [m["market_id"] for m in a] == [m["market_id"] for m in b]


def test_sample_invariant_under_input_order_permutation() -> None:
    """Within-chain order is canonical (market_id); full list order must not change the sample."""
    markets = [_m(f"n{i}") for i in range(7)]
    a = sample_markets_by_chain(markets, per_chain_limit=5)
    b = sample_markets_by_chain(list(reversed(markets)), per_chain_limit=5)
    assert [m["market_id"] for m in a] == [m["market_id"] for m in b]


def test_chain_key_prefers_chain_name_over_chain_id() -> None:
    m_a = {"market_id": "1", "chain_name": "Arbitrum", "chain_id": 42161}
    m_b = {"market_id": "2", "chain_name": None, "chain_id": 42161}
    groups = group_markets_by_chain([m_a, m_b])
    assert set(groups.keys()) == {"Arbitrum", "42161"}


def test_fallback_to_chain_id_when_chain_name_missing() -> None:
    markets = [
        {"market_id": "a", "chain_id": 10},
        {"market_id": "b", "chain_id": 10},
    ]
    out = sample_markets_by_chain(markets, per_chain_limit=5)
    assert [m["market_id"] for m in out] == ["a", "b"]


def test_final_order_is_deterministic_by_sorted_chain_key() -> None:
    markets = [
        _m("z", chain_name="zebra", chain_id=1),
        _m("a", chain_name="alpha", chain_id=2),
    ]
    out = sample_markets_by_chain(markets, per_chain_limit=5)
    assert [m["chain_name"] for m in out] == ["alpha", "zebra"]
