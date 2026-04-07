"""Tests for protocol API / URL comparators and run_protocol_validation flow (TDD)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from validators.browser_validation import BrowserValidationOutcome, EchoStubBrowserAdapter
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

    def list_markets(
        self,
        *,
        protocol: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("list_markets", {"protocol": protocol, "limit": limit}))
        return {"data": self.markets_data, "count": len(self.markets_data)}

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
        "list_markets",
        "attach_validation_evidence",
        "update_protocol_run",
    ]

    create_payload = client.calls[0][1]
    assert create_payload["protocol"] == "aave_v3"
    assert create_payload["actor_type"] == "agent"
    assert create_payload["protocol_run_id"].startswith("run_")

    ev_id, ev_payload = client.calls[2][1], client.calls[2][2]
    assert ev_id == "run_rec_1"
    assert ev_payload["source"] == "api_compare"
    assert ev_payload["market_id"] == "protocol::aave_v3"
    assert ev_payload["actor_type"] == "agent"
    assert "comparisons" in ev_payload["metadata"]

    _uid, upd = client.calls[3][1], client.calls[3][2]
    assert upd["current_phase"] == "completed"
    assert upd["terminal_status"] == "success"

    assert summary["protocol_run_id"] == "run_rec_1"
    assert summary["terminal_status"] == "success"
    assert summary["worst_bucket"] == "PASS"


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
            status="ok",
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
        assert p["metadata"]["status"] == "ok"
        assert p["metadata"]["market_id"] == p["market_id"]
        assert p["summary"] == "synthetic page check"


def test_echo_stub_browser_adapter_returns_stub_status() -> None:
    out = EchoStubBrowserAdapter().validate_market_url(
        {"market_id": "x", "market_url": "https://a.example/x"}
    )
    assert out.resolved_url == "https://a.example/x"
    assert out.status == "stub"
    assert out.metadata.get("adapter") == "EchoStubBrowserAdapter"
