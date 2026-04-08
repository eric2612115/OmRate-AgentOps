"""Protocol-specific adapters: normalize official API payloads to a unified snapshot shape."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

from validators.official_sources import ProtocolOfficialSource, get_protocol_source


@dataclass(frozen=True)
class OfficialMarketSnapshot:
    """Unified market row for matrix comparison (official side)."""

    protocol: str
    chain_id: int
    market_type: str
    expected_market_url: str | None
    supply_apy: float | None
    borrow_apy: float | None
    net_supply_apy: float | None
    net_borrow_apy: float | None
    implied_apy: float | None
    total_supply_usd: float | None
    total_borrow_usd: float | None
    matched_by: str
    source_kind: str
    raw_metadata: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@runtime_checkable
class ProtocolAdapter(Protocol):
    """Normalize a recorded or live official payload for one protocol."""

    protocol_id: str
    source_kind: str

    def normalize_snapshot(
        self,
        market: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> OfficialMarketSnapshot: ...

    def fetch_snapshot(
        self,
        market: Mapping[str, Any],
        source: ProtocolOfficialSource,
    ) -> Mapping[str, Any]: ...


class UnknownProtocolAdapterError(LookupError):
    """No adapter registered for the given protocol id."""


def market_expected_url(market: Mapping[str, Any]) -> str | None:
    raw = market.get("expected_market_url") or market.get("market_url")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def market_debt_symbol(market: Mapping[str, Any], default: str = "") -> str:
    raw = market.get("symbol")
    if raw is None:
        raw = (market.get("debt_token") or {}).get("symbol")
    if raw is None:
        return default
    text = str(raw).strip()
    return text or default


def protocol_runtime_metadata(protocol: str) -> dict[str, Any]:
    return dict(get_protocol_source(protocol).runtime_metadata or {})


def _adapter_registry() -> dict[str, type]:
    from .aave_v3 import AaveV3Adapter
    from .euler_v2 import EulerV2Adapter
    from .fluid import FluidAdapter
    from .lista import ListaAdapter
    from .morpho import MorphoAdapter
    from .pendle import PendleAdapter
    from .termmax import TermmaxAdapter
    from .venus import VenusAdapter

    return {
        "aave_v3": AaveV3Adapter,
        "morpho": MorphoAdapter,
        "euler_v2": EulerV2Adapter,
        "venus": VenusAdapter,
        "fluid": FluidAdapter,
        "termmax": TermmaxAdapter,
        "lista": ListaAdapter,
        "pendle": PendleAdapter,
    }


def list_registered_protocol_ids() -> tuple[str, ...]:
    """Stable ordering for matrix runs (same keys as :func:`build_protocol_adapter`)."""
    return tuple(sorted(_adapter_registry().keys()))


def build_protocol_adapter(protocol: str, transport: Any = None) -> ProtocolAdapter:
    """Factory for protocol adapters. ``transport`` is reserved for future HTTP injection."""
    _registry = _adapter_registry()
    key = protocol.strip()
    cls = _registry.get(key)
    if cls is None:
        raise UnknownProtocolAdapterError(f"unknown protocol adapter: {protocol!r}")
    return cls(transport=transport)


__all__ = [
    "OfficialMarketSnapshot",
    "ProtocolAdapter",
    "UnknownProtocolAdapterError",
    "build_protocol_adapter",
    "list_registered_protocol_ids",
    "market_debt_symbol",
    "market_expected_url",
    "protocol_runtime_metadata",
]
