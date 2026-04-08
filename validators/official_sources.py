"""
Trusted protocol source registry: DefiLlama cross-check vs official web/app/API bases.

DefiLlama is used to verify protocol identity (website / app hosts) against a canonical
listing; it is not treated as a market-data truth source.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

DEFILLAMA_PROTOCOL_PAGE_BASE = "https://defillama.com/protocol"


class UnknownProtocolError(LookupError):
    """Raised when a protocol id is not present in the trusted registry."""


@dataclass(frozen=True)
class DefiLlamaVerificationSource:
    """DefiLlama-side reference for cross-checking official links (not market data)."""

    slug: str

    @property
    def protocol_page_url(self) -> str:
        return f"{DEFILLAMA_PROTOCOL_PAGE_BASE}/{self.slug}"


@dataclass(frozen=True)
class ProtocolOfficialSource:
    """One protocol: verification slug, official site, trusted app hosts, optional API base."""

    protocol_id: str
    defillama: DefiLlamaVerificationSource
    official_website_url: str
    trusted_app_hosts: tuple[str, ...]
    official_api_base: str | None
    official_source_kind: str
    market_url_templates: tuple[str, ...]
    market_url_notes: tuple[str, ...] = ()
    runtime_metadata: dict[str, Any] | None = None

OFFICIAL_SOURCES_RELATIVE_PATH = "protocol-rules/official-sources.yaml"
OFFICIAL_SOURCES_PATH = Path(__file__).resolve().parents[1] / OFFICIAL_SOURCES_RELATIVE_PATH


@lru_cache(maxsize=1)
def _load_protocol_registry() -> dict[str, ProtocolOfficialSource]:
    payload = yaml.safe_load(OFFICIAL_SOURCES_PATH.read_text())
    raw_protocols = payload.get("protocols") or {}
    registry: dict[str, ProtocolOfficialSource] = {}
    for protocol_id, raw in raw_protocols.items():
        registry[protocol_id] = ProtocolOfficialSource(
            protocol_id=protocol_id,
            defillama=DefiLlamaVerificationSource(slug=str(raw["defillama_slug"])),
            official_website_url=str(raw["official_website_url"]),
            trusted_app_hosts=tuple(str(v) for v in raw.get("trusted_app_hosts") or ()),
            official_api_base=raw.get("official_api_base"),
            official_source_kind=str(raw["official_source_kind"]),
            market_url_templates=tuple(
                str(v) for v in raw.get("market_url_templates") or ()
            ),
            market_url_notes=tuple(str(v) for v in raw.get("market_url_notes") or ()),
            runtime_metadata=dict(raw.get("runtime_metadata") or {}),
        )
    return registry


PROTOCOL_IDS: tuple[str, ...] = tuple(sorted(_load_protocol_registry().keys()))


def get_protocol_source(protocol: str) -> ProtocolOfficialSource:
    key = protocol.strip()
    try:
        return _load_protocol_registry()[key]
    except KeyError as e:
        raise UnknownProtocolError(f"unknown protocol id: {protocol!r}") from e


def _hostname_for_url(url: str) -> str | None:
    s = url.strip()
    if not s:
        return None
    if "://" not in s:
        s = f"https://{s}"
    host = urlparse(s).hostname
    return host.lower() if host else None


def is_trusted_app_host(protocol: str, url: str) -> bool:
    src = get_protocol_source(protocol)
    host = _hostname_for_url(url)
    if host is None:
        return False
    trusted = {h.lower() for h in src.trusted_app_hosts}
    return host in trusted


def build_protocol_website_check(protocol: str) -> dict[str, Any]:
    """
    Structured metadata for validating official website vs trusted app surfaces.

    Separates DefiLlama verification reference, official marketing site, app hosts,
    and optional official API base.
    """
    src = get_protocol_source(protocol)
    return {
        "protocol_id": src.protocol_id,
        "defillama": {
            "slug": src.defillama.slug,
            "protocol_page_url": src.defillama.protocol_page_url,
        },
        "official_website_url": src.official_website_url,
        "trusted_app_hosts": list(src.trusted_app_hosts),
        "official_api_base": src.official_api_base,
        "official_source_kind": src.official_source_kind,
        "market_url_templates": list(src.market_url_templates),
        "market_url_notes": list(src.market_url_notes),
        "runtime_metadata": dict(src.runtime_metadata or {}),
        "local_reference_file": OFFICIAL_SOURCES_RELATIVE_PATH,
    }
