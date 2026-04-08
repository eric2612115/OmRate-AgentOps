"""Tests for trusted protocol source registry and website-check helpers (TDD)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from validators.official_sources import (
    PROTOCOL_IDS,
    UnknownProtocolError,
    build_protocol_website_check,
    get_protocol_source,
    is_trusted_app_host,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_registry_contains_all_eight_protocols() -> None:
    assert len(PROTOCOL_IDS) == 8
    expected = {
        "aave_v3",
        "morpho",
        "euler_v2",
        "venus",
        "fluid",
        "termmax",
        "lista",
        "pendle",
    }
    assert set(PROTOCOL_IDS) == expected


@pytest.mark.parametrize(
    ("protocol", "url", "expected"),
    [
        ("aave_v3", "https://app.aave.com/markets", True),
        ("aave_v3", "https://APP.AAVE.COM/", True),
        ("aave_v3", "https://evil.com/app.aave.com", False),
        ("morpho", "https://app.morpho.org/", True),
        ("euler_v2", "https://app.euler.finance/dashboard", True),
        ("venus", "https://app.venus.io/", True),
        ("fluid", "https://fluid.io/lend", True),
        ("termmax", "https://app.termmax.ts.finance/", True),
        ("lista", "https://lista.org/app", True),
        ("pendle", "https://app.pendle.finance/trade", True),
        ("aave_v3", "https://aave.com/", False),
    ],
)
def test_is_trusted_app_host(protocol: str, url: str, expected: bool) -> None:
    assert is_trusted_app_host(protocol, url) is expected


def test_unknown_protocol_get_protocol_source_raises() -> None:
    with pytest.raises(UnknownProtocolError) as exc_info:
        get_protocol_source("not_a_protocol")
    msg = str(exc_info.value).lower()
    assert "unknown" in msg and "not_a_protocol" in msg


def test_unknown_protocol_is_trusted_app_host_raises() -> None:
    with pytest.raises(UnknownProtocolError):
        is_trusted_app_host("fake_proto", "https://example.com")


def test_get_protocol_source_separates_defillama_website_api_and_hosts() -> None:
    src = get_protocol_source("aave_v3")
    assert src.protocol_id == "aave_v3"
    assert src.defillama.slug == "aave-v3"
    assert src.defillama.protocol_page_url == (
        "https://defillama.com/protocol/aave-v3"
    )
    assert src.official_website_url == "https://aave.com/"
    assert src.trusted_app_hosts == ("app.aave.com",)
    assert src.official_api_base == "https://api.v3.aave.com/graphql"
    assert src.official_source_kind == "graphql"
    assert src.market_url_templates == (
        "https://app.aave.com/reserve-overview/?underlyingAsset={debt_token_address}&marketName={market_name}",
    )


def test_onchain_runtime_metadata_addresses_stay_strings() -> None:
    fluid = get_protocol_source("fluid")
    lista = get_protocol_source("lista")
    assert isinstance(fluid.runtime_metadata["resolver_addresses"][1], str)
    assert isinstance(lista.runtime_metadata["contracts"]["jug"], str)
    assert isinstance(lista.runtime_metadata["collaterals"][0]["address"], str)


def test_build_protocol_website_check_returns_metadata_dict() -> None:
    meta = build_protocol_website_check("morpho")
    assert meta["protocol_id"] == "morpho"
    assert "defillama" in meta
    assert "slug" in meta["defillama"]
    assert "protocol_page_url" in meta["defillama"]
    assert meta["official_website_url"].startswith("https://")
    assert isinstance(meta["trusted_app_hosts"], (list, tuple))
    assert "official_api_base" in meta
    assert meta["official_source_kind"] == "graphql"
    assert meta["market_url_templates"] == [
        "https://app.morpho.org/{network}/market/{unique_key}/{pair_slug}"
    ]
    assert meta["local_reference_file"].endswith("protocol-rules/official-sources.yaml")


def test_cli_outputs_json_for_single_protocol() -> None:
    script = REPO_ROOT / "scripts" / "check_protocol_websites.py"
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--protocol",
            "pendle",
            "--json",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["protocol_id"] == "pendle"
    assert data["defillama"]["slug"]
