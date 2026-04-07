"""Pluggable browser-side validation for market URLs (no MCP coupling)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class BrowserValidationOutcome:
    """Result of validating a market page (or URL) in a browser-like environment."""

    resolved_url: str
    status: str
    summary: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


class BrowserValidationAdapter(Protocol):
    """Implementations perform real browser checks; tests use lightweight fakes."""

    def validate_market_url(self, market: dict[str, Any]) -> BrowserValidationOutcome:
        """Validate ``market`` (expects ``market_url``, ``market_id`` when available)."""
        ...


class EchoStubBrowserAdapter:
    """Smoke adapter: echoes ``market_url`` as ``resolved_url`` without external I/O."""

    def validate_market_url(self, market: dict[str, Any]) -> BrowserValidationOutcome:
        raw = market.get("market_url") or ""
        return BrowserValidationOutcome(
            resolved_url=raw,
            status="stub",
            summary="echo stub (no browser)",
            metadata={"adapter": "EchoStubBrowserAdapter"},
        )
