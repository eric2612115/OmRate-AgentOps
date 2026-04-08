"""Deterministic per-chain sampling for live validation market lists."""

from __future__ import annotations

from typing import Any


def chain_key_for_market(market: dict[str, Any]) -> str:
    """
    Prefer non-empty ``chain_name``; otherwise ``chain_id`` as string; else empty string.
    """
    name = market.get("chain_name")
    if name is not None and str(name).strip() != "":
        return str(name)
    cid = market.get("chain_id")
    if cid is not None:
        return str(cid)
    return ""


def _eligible(market: dict[str, Any]) -> bool:
    if market.get("is_expired") is True:
        return False
    if market.get("is_disabled") is True:
        return False
    return True


def group_markets_by_chain(markets: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """
    Eligible markets only, grouped by chain key; each group sorted by ``market_id``.
    """
    eligible = [m for m in markets if _eligible(m)]
    groups: dict[str, list[dict[str, Any]]] = {}
    for m in eligible:
        key = chain_key_for_market(m)
        groups.setdefault(key, []).append(m)
    for key in groups:
        groups[key].sort(key=lambda m: str(m.get("market_id") or ""))
    return groups


def sample_markets_by_chain(
    markets: list[dict[str, Any]],
    *,
    per_chain_limit: int = 5,
) -> list[dict[str, Any]]:
    """
    Per chain (after eligibility filter), take up to ``per_chain_limit`` markets.
    Order: chains sorted by key, markets in canonical ``market_id`` order within each chain.
    """
    groups = group_markets_by_chain(markets)
    out: list[dict[str, Any]] = []
    for ck in sorted(groups.keys()):
        out.extend(groups[ck][:per_chain_limit])
    return out


def sampling_metadata(
    raw_markets: list[dict[str, Any]],
    sampled: list[dict[str, Any]],
) -> dict[str, Any]:
    """Evidence-friendly counts and sorted chain keys present in the sample."""
    keys = sorted({chain_key_for_market(m) for m in sampled})
    return {
        "list_market_count": len(raw_markets),
        "sampled_market_count": len(sampled),
        "sampled_chain_count": len(keys),
        "sampled_chain_keys": keys,
    }
