"""Tests for protocol API / URL comparators and run_protocol_validation flow (TDD)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from validators.browser_validation import (
    BROWSER_STATUS_BLOCKED,
    BROWSER_STATUS_FAIL,
    BROWSER_STATUS_PASS,
    BROWSER_STATUS_STUB,
    _accept_unverified_vault_modal,
    _detect_blocked_reason,
    _extract_page_markers,
    BrowserPageSnapshot,
    BrowserValidationOutcome,
    EchoStubBrowserAdapter,
    RealBrowserValidationAdapter,
)
from validators.manifest_loader import load_manifest
from validators.protocol_api_compare import (
    aggregate_worst_bucket,
    compare_market_metrics,
    run_protocol_validation,
)
from validators.url_validation import compare_market_urls, normalize_url


def test_compare_market_metrics_pass_small_diff() -> None:
    backend = {"supply_apy": 3.0, "borrow_apy": 4.0}
    official = {"supply_apy": 3.04, "borrow_apy": 4.0}
    out = compare_market_metrics(
        backend,
        official,
        apy_fields=("supply_apy", "borrow_apy"),
        warn_abs_diff=0.05,
        bug_abs_diff=0.5,
    )
    assert out["bucket"] == "PASS"
    assert all(f["bucket"] == "PASS" for f in out["fields"])


def test_compare_market_metrics_warn_moderate_diff() -> None:
    backend = {"supply_apy": 3.0, "borrow_apy": 4.0}
    official = {"supply_apy": 3.2, "borrow_apy": 4.0}
    out = compare_market_metrics(
        backend,
        official,
        apy_fields=("supply_apy", "borrow_apy"),
        warn_abs_diff=0.05,
        bug_abs_diff=0.5,
    )
    assert out["bucket"] == "WARN"
    supply = next(f for f in out["fields"] if f["field"] == "supply_apy")
    assert supply["bucket"] == "WARN"
    assert supply["abs_diff"] == pytest.approx(0.2)


def test_compare_market_metrics_bug_large_diff() -> None:
    backend = {"supply_apy": 3.0, "borrow_apy": 4.0}
    official = {"supply_apy": 8.0, "borrow_apy": 4.0}
    out = compare_market_metrics(
        backend,
        official,
        apy_fields=("supply_apy", "borrow_apy"),
        warn_abs_diff=0.05,
        bug_abs_diff=0.5,
    )
    assert out["bucket"] == "BUG"
    supply = next(f for f in out["fields"] if f["field"] == "supply_apy")
    assert supply["bucket"] == "BUG"


def test_compare_market_metrics_both_none_is_pass_for_field() -> None:
    backend = {"supply_apy": None, "borrow_apy": 2.0}
    official = {"supply_apy": None, "borrow_apy": 2.01}
    out = compare_market_metrics(
        backend,
        official,
        apy_fields=("supply_apy", "borrow_apy"),
        warn_abs_diff=0.05,
        bug_abs_diff=0.5,
    )
    supply = next(f for f in out["fields"] if f["field"] == "supply_apy")
    assert supply["bucket"] == "PASS"


def test_compare_market_metrics_one_side_none_is_bug() -> None:
    backend = {"supply_apy": 3.0, "borrow_apy": None}
    official = {"supply_apy": None, "borrow_apy": None}
    out = compare_market_metrics(
        backend,
        official,
        apy_fields=("supply_apy", "borrow_apy"),
        warn_abs_diff=0.05,
        bug_abs_diff=0.5,
    )
    assert out["bucket"] == "BUG"


def test_aggregate_worst_bucket_order() -> None:
    assert aggregate_worst_bucket(["PASS", "WARN", "PASS"]) == "WARN"
    assert aggregate_worst_bucket(["PASS", "BUG", "WARN"]) == "BUG"
    assert aggregate_worst_bucket(["PASS"]) == "PASS"


def test_normalize_url_strips_trailing_slash_and_lowers_host() -> None:
    a = normalize_url("HTTPS://App.Example.COM/path/")
    b = normalize_url("https://app.example.com/path")
    assert a == b


def test_compare_market_urls_pass_when_normalized_equal() -> None:
    out = compare_market_urls(
        "https://app.example.com/pool/1/",
        "https://app.example.com/pool/1",
    )
    assert out["bucket"] == "PASS"


def test_compare_market_urls_bug_when_netloc_differs() -> None:
    out = compare_market_urls(
        "https://a.example.com/x",
        "https://b.example.com/x",
    )
    assert out["bucket"] == "BUG"


def test_compare_market_urls_warn_same_site_different_path() -> None:
    out = compare_market_urls(
        "https://app.example.com/pool/1",
        "https://app.example.com/pool/2",
    )
    assert out["bucket"] == "WARN"


def test_compare_market_urls_warn_same_site_different_query() -> None:
    out = compare_market_urls(
        "https://app.example.com/pool/1?underlyingAsset=0xabc&marketName=proto_mainnet_v3",
        "https://app.example.com/pool/1?underlyingAsset=0xdef&marketName=proto_mainnet_v3",
    )
    assert out["bucket"] == "WARN"


def test_compare_market_urls_warn_same_site_different_hash_route() -> None:
    out = compare_market_urls(
        "https://venus.io/market#/markets/0xpool/0xmarket-a?chainId=56&tab=supply",
        "https://venus.io/market#/markets/0xpool/0xmarket-b?chainId=56&tab=supply",
    )
    assert out["bucket"] == "WARN"


def test_normalize_url_sorts_fragment_query_params() -> None:
    a = normalize_url("https://venus.io/market#/markets/0xpool/0xmarket?tab=supply&chainId=56")
    b = normalize_url("https://venus.io/market#/markets/0xpool/0xmarket?chainId=56&tab=supply")
    assert a == b


def test_compare_market_urls_bug_one_side_empty() -> None:
    assert compare_market_urls("", "https://x.com/a")["bucket"] == "BUG"
    assert compare_market_urls("https://x.com/a", "")["bucket"] == "BUG"


def test_compare_market_urls_pass_both_empty() -> None:
    assert compare_market_urls(None, None)["bucket"] == "PASS"


class _RecordingBackend:
    """Minimal stand-in for BackendValidationClient in flow tests."""

    def __init__(self, markets_data: list[dict[str, Any]]) -> None:
        self.markets_data = markets_data
        self.calls: list[tuple[str, Any]] = []

    def create_protocol_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("create_protocol_run", payload))
        return {"protocol_run_id": "run_rec_1"}

    def list_protocols(self) -> dict[str, Any]:
        self.calls.append(("list_protocols", None))
        return {"data": []}

    def list_markets(
        self,
        *,
        protocol: str | None = None,
        chain_id: int | None = None,
        limit: int | None = None,
        is_expired: bool | None = None,
        is_disabled: bool | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "list_markets",
                {
                    "protocol": protocol,
                    "chain_id": chain_id,
                    "limit": limit,
                    "is_expired": is_expired,
                    "is_disabled": is_disabled,
                },
            )
        )
        rows = list(self.markets_data)
        if chain_id is not None:
            rows = [row for row in rows if int(row.get("chain_id") or 0) == chain_id]
        return {"data": rows, "count": len(rows)}

    def attach_validation_evidence(
        self,
        protocol_run_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(("attach_validation_evidence", protocol_run_id, payload))
        return {"ok": True}

    def update_protocol_run(
        self,
        protocol_run_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(("update_protocol_run", protocol_run_id, payload))
        return {"ok": True}


def test_run_protocol_validation_end_to_end(tmp_path: Path) -> None:
    markets = [
        {
            "market_id": "m1",
            "supply_apy": 3.0,
            "borrow_apy": 4.0,
            "market_url": "https://app.example.com/pool/1",
        }
    ]
    client = _RecordingBackend(markets)

    def official_for_market(_m: dict[str, Any]) -> dict[str, Any]:
        return {
            "supply_apy": 3.01,
            "borrow_apy": 4.0,
            "expected_market_url": "https://app.example.com/pool/1",
        }

    manifest_path = tmp_path / "run_manifest.yaml"
    summary = run_protocol_validation(
        client,  # type: ignore[arg-type]
        protocol="aave_v3",
        limit=10,
        ruleset_version="ruleset-test",
        manifest_path=manifest_path,
        official_for_market=official_for_market,
    )

    assert manifest_path.is_file()
    loaded = load_manifest(manifest_path)
    assert loaded["protocol"] == "aave_v3"
    assert loaded["ruleset_version"] == "ruleset-test"

    kinds = [c[0] for c in client.calls]
    assert kinds == [
        "create_protocol_run",
        "list_protocols",
        "list_markets",
        "attach_validation_evidence",
        "update_protocol_run",
    ]
    assert client.calls[2][1]["is_expired"] is False
    assert client.calls[2][1]["is_disabled"] is False

    create_payload = client.calls[0][1]
    assert create_payload["protocol"] == "aave_v3"
    assert create_payload["actor_type"] == "agent"
    assert create_payload["protocol_run_id"].startswith("run_")

    ev_id, ev_payload = client.calls[3][1], client.calls[3][2]
    assert ev_id == "run_rec_1"
    assert ev_payload["source"] == "api_compare"
    assert ev_payload["market_id"] == "protocol::aave_v3"
    assert ev_payload["actor_type"] == "agent"
    assert "comparisons" in ev_payload["metadata"]

    _uid, upd = client.calls[4][1], client.calls[4][2]
    assert upd["current_phase"] == "completed"
    assert upd["terminal_status"] == "success"

    assert summary["protocol_run_id"] == "run_rec_1"
    assert summary["terminal_status"] == "success"
    assert summary["worst_bucket"] == "PASS"

    meta = client.calls[3][2]["metadata"]
    assert meta["sampled_market_count"] == 1
    assert meta["sampled_chain_count"] == 1
    assert meta["sampled_chain_keys"] == [""]


def test_run_protocol_validation_only_compares_sampled_markets_and_metadata(
    tmp_path: Path,
) -> None:
    """More than per_chain_limit on one chain -> compare capped count; evidence has sample stats."""
    markets = [
        {
            "market_id": f"m{i}",
            "chain_name": "ethereum",
            "chain_id": 1,
            "is_expired": False,
            "is_disabled": False,
            "supply_apy": 3.0,
            "borrow_apy": 4.0,
            "market_url": f"https://app.example.com/pool/{i}",
        }
        for i in range(6)
    ]
    client = _RecordingBackend(markets)

    def official_for_market(m: dict[str, Any]) -> dict[str, Any]:
        return {
            "supply_apy": m["supply_apy"],
            "borrow_apy": m["borrow_apy"],
            "expected_market_url": m.get("market_url"),
        }

    summary = run_protocol_validation(
        client,  # type: ignore[arg-type]
        protocol="aave_v3",
        limit=50,
        ruleset_version="ruleset-test",
        manifest_path=tmp_path / "sample_cap.yaml",
        official_for_market=official_for_market,
        per_chain_limit=5,
    )

    assert len(summary["comparisons"]) == 5
    meta = client.calls[3][2]["metadata"]
    assert meta["sampled_market_count"] == 5
    assert meta["sampled_chain_count"] == 1
    assert meta["sampled_chain_keys"] == ["ethereum"]
    assert meta["list_market_count"] == 6
    compared_ids = {c["market_id"] for c in meta["comparisons"]}
    assert compared_ids == {"m0", "m1", "m2", "m3", "m4"}


def test_run_protocol_validation_terminal_failed_on_bug(tmp_path: Path) -> None:
    markets = [
        {
            "market_id": "m_bug",
            "supply_apy": 1.0,
            "borrow_apy": 2.0,
            "market_url": "https://app.example.com/a",
        }
    ]
    client = _RecordingBackend(markets)

    def official_for_market(_m: dict[str, Any]) -> dict[str, Any]:
        return {
            "supply_apy": 99.0,
            "borrow_apy": 2.0,
            "expected_market_url": "https://app.example.com/a",
        }

    summary = run_protocol_validation(
        client,  # type: ignore[arg-type]
        protocol="morpho",
        limit=5,
        ruleset_version="v1",
        manifest_path=tmp_path / "m.yaml",
        official_for_market=official_for_market,
    )
    assert summary["worst_bucket"] == "BUG"
    upd = client.calls[-1][2]
    assert upd["terminal_status"] == "failed"


class _FakeBrowserAdapter:
    def validate_market_url(self, market: dict[str, Any]) -> BrowserValidationOutcome:
        mid = str(market.get("market_id") or "")
        return BrowserValidationOutcome(
            resolved_url=f"https://resolved.example/view/{mid}",
            status=BROWSER_STATUS_PASS,
            summary="synthetic page check",
            metadata={"http_status": 200, "market_id": mid},
        )


def test_run_protocol_validation_attaches_browser_evidence_per_market(tmp_path: Path) -> None:
    markets = [
        {
            "market_id": "m1",
            "supply_apy": 3.0,
            "borrow_apy": 4.0,
            "market_url": "https://app.example.com/pool/1",
        },
        {
            "market_id": "m2",
            "supply_apy": 1.0,
            "borrow_apy": 2.0,
            "market_url": "https://app.example.com/pool/2",
        },
    ]
    client = _RecordingBackend(markets)

    def official_for_market(_m: dict[str, Any]) -> dict[str, Any]:
        return {
            "supply_apy": _m["supply_apy"],
            "borrow_apy": _m["borrow_apy"],
            "expected_market_url": _m.get("market_url"),
        }

    manifest_path = tmp_path / "run_browser.yaml"
    run_protocol_validation(
        client,  # type: ignore[arg-type]
        protocol="aave_v3",
        limit=10,
        ruleset_version="ruleset-test",
        manifest_path=manifest_path,
        official_for_market=official_for_market,
        browser_adapter=_FakeBrowserAdapter(),
    )

    attach_calls = [c for c in client.calls if c[0] == "attach_validation_evidence"]
    assert len(attach_calls) == 3
    api_ev = attach_calls[0][2]
    assert api_ev["source"] == "api_compare"
    assert api_ev["market_id"] == "protocol::aave_v3"

    browser_payloads = [c[2] for c in attach_calls[1:]]
    assert {p["market_id"] for p in browser_payloads} == {"m1", "m2"}
    for p in browser_payloads:
        assert p["source"] == "browser"
        assert p["actor_type"] == "agent"
        assert "resolved_url" in p["metadata"]
        assert p["metadata"]["status"] == BROWSER_STATUS_PASS
        assert p["metadata"]["market_id"] == p["market_id"]
        assert p["summary"] == "synthetic page check"


class _FakeInspector:
    def __init__(self, snapshot: BrowserPageSnapshot) -> None:
        self.snapshot = snapshot

    def inspect_market_page(self, market: dict[str, Any]) -> BrowserPageSnapshot:
        return self.snapshot


def test_real_browser_validation_adapter_returns_pass_for_matching_url_and_markers() -> None:
    market = {
        "protocol": "aave_v3",
        "chain_name": "Ethereum",
        "symbol": "USDC",
        "market_url": "https://app.aave.com/reserve-overview/?underlyingAsset=0xabc&marketName=proto_mainnet_v3",
    }
    adapter = RealBrowserValidationAdapter(
        _FakeInspector(
            BrowserPageSnapshot(
                resolved_url="https://app.aave.com/reserve-overview/?underlyingAsset=0xabc&marketName=proto_mainnet_v3",
                page_markers=("aave_v3", "ethereum", "usdc"),
                http_status=200,
            )
        )
    )
    out = adapter.validate_market_url(market)
    assert out.status == BROWSER_STATUS_PASS
    assert out.metadata["missing_markers"] == []


def test_real_browser_validation_adapter_returns_fail_for_url_mismatch() -> None:
    market = {
        "protocol": "pendle",
        "chain_name": "Ethereum",
        "symbol": "stETH",
        "market_url": "https://app.pendle.finance/trade/markets/0xpool?chain=ethereum",
    }
    adapter = RealBrowserValidationAdapter(
        _FakeInspector(
            BrowserPageSnapshot(
                resolved_url="https://app.pendle.finance/trade/markets/0xother?chain=ethereum",
                page_markers=("pendle", "ethereum", "steth"),
                http_status=200,
            )
        )
    )
    out = adapter.validate_market_url(market)
    assert out.status == BROWSER_STATUS_FAIL
    assert out.metadata["url_bucket"] == "WARN"


def test_real_browser_validation_adapter_returns_blocked_for_login_marker() -> None:
    market = {
        "protocol": "morpho",
        "chain_name": "Base",
        "symbol": "USDC",
        "market_url": "https://app.morpho.org/market?id=abc&network=base",
    }
    adapter = RealBrowserValidationAdapter(
        _FakeInspector(
            BrowserPageSnapshot(
                resolved_url="https://app.morpho.org/market?id=abc&network=base",
                page_markers=("login",),
                http_status=200,
            )
        )
    )
    out = adapter.validate_market_url(market)
    assert out.status == BROWSER_STATUS_BLOCKED
    assert out.metadata["blocked_reason"] == "login_required"


def test_extract_page_markers_maps_protocol_aliases_to_expected_markers() -> None:
    market = {
        "protocol": "aave_v3",
        "chain_name": "Ethereum",
        "debt_token": {"symbol": "USDC"},
    }
    markers = _extract_page_markers(
        market,
        resolved_url="https://app.aave.com/reserve-overview/?underlyingAsset=0xabc",
        title="Aave | Ethereum USDC",
        body_text="Borrow USDC on Ethereum in the Aave market.",
    )
    assert "aave_v3" in markers
    assert "ethereum" in markers
    assert "usdc" in markers


def test_extract_page_markers_matches_symbol_when_ui_concatenates_words() -> None:
    market = {
        "protocol": "aave_v3",
        "chain_name": "Arbitrum",
        "debt_token": {"symbol": "FRAX"},
    }
    markers = _extract_page_markers(
        market,
        resolved_url="https://app.aave.com/reserve-overview/?underlyingAsset=0x17fc&marketName=proto_arbitrum_v3",
        title="Aave - Open Source Liquidity Protocol",
        body_text="Arbitrum Market Version 3 FRAXFraxReserve Size",
    )
    assert "frax" in markers


def test_extract_page_markers_matches_arbitrumone_chain_alias() -> None:
    market = {
        "protocol": "euler_v2",
        "chain_name": "Arbitrum",
        "debt_token": {"symbol": "USDC"},
    }
    markers = _extract_page_markers(
        market,
        resolved_url="https://app.euler.finance/vault/0xabc?network=arbitrumone",
        title="Vault | Euler Finance",
        body_text="Ungoverned USDC vault",
    )
    assert "arbitrum" in markers


def test_detect_blocked_reason_prefers_text_markers_over_status_code() -> None:
    reason = _detect_blocked_reason(
        resolved_url="https://app.example.com/blocked",
        title="Attention Required",
        body_text="Cloudflare says verify you are human before continuing.",
        http_status=200,
    )
    assert reason == "captcha"


def test_detect_blocked_reason_marks_aave_legacy_shell_blocked() -> None:
    reason = _detect_blocked_reason(
        resolved_url="https://app.aave.com/reserve-overview/?underlyingAsset=0xabc&marketName=proto_mainnet_v3",
        title="Aave - Open Source Liquidity Protocol",
        body_text="Dashboard Legacy Markets Global settings",
        http_status=200,
    )
    assert reason == "legacy_market_shell"


def test_detect_blocked_reason_ignores_euler_indexer_sync_banner() -> None:
    reason = _detect_blocked_reason(
        resolved_url="https://app.euler.finance/vault/0xabc?network=arbitrumone",
        title="Vault | Euler Finance",
        body_text=(
            "Indexer Synchronizing. The indexer is currently synchronizing. "
            "Some data may be temporarily unavailable or outdated."
        ),
        http_status=200,
    )
    assert reason is None


def test_accept_unverified_vault_modal_clicks_yes() -> None:
    class _FakeLocator:
        def __init__(self) -> None:
            self.clicked = False

        def count(self) -> int:
            return 1

        @property
        def first(self) -> "_FakeLocator":
            return self

        def click(self, timeout: int = 0) -> None:
            self.clicked = True

    class _FakePage:
        def __init__(self) -> None:
            self.locator = _FakeLocator()

        def evaluate(self, script: str) -> str:
            return "Are you sure you want to interact with the unverified vault?"

        def get_by_role(self, role: str, name: str) -> _FakeLocator:
            assert role == "button"
            assert name == "Yes"
            return self.locator

        def wait_for_timeout(self, timeout_ms: int) -> None:
            return None

    page = _FakePage()
    assert _accept_unverified_vault_modal(page, "https://app.euler.finance/vault/0xabc?network=arbitrumone") is True
    assert page.locator.clicked is True


def test_real_browser_expected_markers_use_protocol_only_for_lista() -> None:
    market = {
        "protocol": "lista",
        "chain_name": "BNB Chain",
        "debt_token": {"symbol": "lisUSD"},
        "market_url": "https://lista.org/dashboard",
    }
    adapter = RealBrowserValidationAdapter(
        _FakeInspector(
            BrowserPageSnapshot(
                resolved_url="https://lista.org/dashboard",
                page_markers=("lista",),
                http_status=200,
            )
        )
    )
    out = adapter.validate_market_url(market)
    assert out.status == BROWSER_STATUS_PASS
    assert out.metadata["required_markers"] == ["lista"]


def test_echo_stub_browser_adapter_returns_stub_status() -> None:
    out = EchoStubBrowserAdapter().validate_market_url(
        {"market_id": "x", "market_url": "https://a.example/x"}
    )
    assert out.resolved_url == "https://a.example/x"
    assert out.status == BROWSER_STATUS_STUB
    assert out.metadata.get("adapter") == "EchoStubBrowserAdapter"
