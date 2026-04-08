"""Tests for live validation report schema contract (TDD)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from validators.report_schema import (
    LiveValidationReport,
    LiveValidationRow,
    ReportSchemaError,
    build_live_validation_report,
    dump_report,
    live_validation_matrix_exit_code,
    load_report,
    matrix_status_from_rows,
    validate_report,
    validate_row,
)


def _row(**kwargs: object) -> LiveValidationRow:
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
    return LiveValidationRow(**base)  # type: ignore[arg-type]


def test_validate_row_rejects_missing_required_semantics() -> None:
    r = _row()
    validate_row(r)
    with pytest.raises(ReportSchemaError):
        validate_row(_row(protocol=""))
    with pytest.raises(ReportSchemaError):
        validate_row(_row(market_id=""))
    with pytest.raises(ReportSchemaError):
        validate_row(_row(sample_rank=0))
    with pytest.raises(ReportSchemaError):
        validate_row(_row(evidence_refs=[]))


def test_matrix_status_priority_blocked_over_fail() -> None:
    assert matrix_status_from_rows([_row(final_bucket="FAIL"), _row(final_bucket="BLOCKED")]) == "BLOCKED"


def test_matrix_status_fail_when_any_fail() -> None:
    assert matrix_status_from_rows([_row(final_bucket="PASS"), _row(final_bucket="FAIL")]) == "FAIL"


def test_matrix_status_pass_all_pass() -> None:
    assert matrix_status_from_rows([_row(), _row(market_id="m2")]) == "PASS"


def test_matrix_status_dry_run_always_dry_run_blocked() -> None:
    assert matrix_status_from_rows([_row()], dry_run=True) == "DRY_RUN_BLOCKED"


def test_matrix_status_empty_rows_pass() -> None:
    assert matrix_status_from_rows([], dry_run=False) == "PASS"


def test_build_live_validation_report_totals_and_blocked_rows() -> None:
    rep = build_live_validation_report(
        run_id="run-1",
        ruleset_version="1.0.0",
        rows=[
            _row(market_id="a", final_bucket="PASS", browser_status="PASS"),
            _row(
                market_id="b",
                final_bucket="FAIL",
                protocol="morpho",
                chain_id=2,
                browser_status="PASS",
                api_bucket="BUG",
            ),
            _row(
                market_id="c",
                final_bucket="BLOCKED",
                chain_id=137,
                browser_status="BLOCKED",
            ),
        ],
        official_data_mode="live",
        browser_runtime_mode="real",
        generated_at="2026-01-01T00:00:00+00:00",
        dry_run=False,
    )
    assert rep.matrix_status == "BLOCKED"
    assert rep.totals_by_bucket == {"PASS": 1, "FAIL": 1, "BLOCKED": 1}
    assert rep.blocked_rows == [
        {"protocol": "aave_v3", "chain_id": 137, "market_id": "c"},
    ]
    validate_report(rep)


def test_build_live_validation_report_dry_run_matrix_status() -> None:
    rep = build_live_validation_report(
        run_id="r",
        ruleset_version="0.1.0",
        rows=[_row(browser_status="PASS")],
        official_data_mode="live",
        browser_runtime_mode="real",
        dry_run=True,
    )
    assert rep.matrix_status == "DRY_RUN_BLOCKED"


def test_build_live_validation_report_infers_browser_runtime_mode_from_rows() -> None:
    rep = build_live_validation_report(
        run_id="r2",
        ruleset_version="0.1.0",
        rows=[_row(browser_status="PASS")],
        official_data_mode="live",
    )
    assert rep.browser_runtime_mode == "real"

    rep_stub = build_live_validation_report(
        run_id="r3",
        ruleset_version="0.1.0",
        rows=[_row(browser_status="STUB")],
        official_data_mode="recorded",
    )
    assert rep_stub.browser_runtime_mode == "stub"


def test_dump_load_roundtrip(tmp_path) -> None:
    rep = build_live_validation_report(
        run_id="rid",
        ruleset_version="2.0.0",
        rows=[_row(market_id="x", evidence_refs=["a", "b"], browser_status="PASS")],
        official_data_mode="live",
        browser_runtime_mode="real",
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    )
    path = tmp_path / "report.json"
    dump_report(rep, path)
    loaded = load_report(path)
    assert loaded.run_id == rep.run_id
    assert loaded.ruleset_version == rep.ruleset_version
    assert loaded.matrix_status == rep.matrix_status
    assert loaded.totals_by_bucket == rep.totals_by_bucket
    assert len(loaded.rows) == 1
    assert loaded.rows[0].market_id == "x"
    assert list(loaded.rows[0].evidence_refs) == ["a", "b"]


def test_load_report_rejects_malformed_json() -> None:
    with pytest.raises(ReportSchemaError):
        load_report(__import__("io").StringIO('{"run_id":'))


def test_live_validation_matrix_exit_code_rules() -> None:
    assert (
        live_validation_matrix_exit_code(
            matrix_status="PASS",
            dry_run=False,
            official_data_mode="live",
            browser_runtime_mode="real",
        )
        == 0
    )
    assert (
        live_validation_matrix_exit_code(
            matrix_status="PASS",
            dry_run=False,
            official_data_mode="recorded",
            browser_runtime_mode="real",
        )
        == 4
    )
    assert (
        live_validation_matrix_exit_code(
            matrix_status="PASS",
            dry_run=False,
            official_data_mode="live",
            browser_runtime_mode="stub",
        )
        == 4
    )
    assert (
        live_validation_matrix_exit_code(
            matrix_status="FAIL",
            dry_run=False,
            official_data_mode="live",
            browser_runtime_mode="real",
        )
        == 1
    )
    assert (
        live_validation_matrix_exit_code(
            matrix_status="BLOCKED",
            dry_run=False,
            official_data_mode="live",
            browser_runtime_mode="real",
        )
        == 1
    )
    assert (
        live_validation_matrix_exit_code(
            matrix_status="PASS",
            dry_run=True,
            official_data_mode="live",
            browser_runtime_mode="real",
        )
        == 3
    )
    assert (
        live_validation_matrix_exit_code(
            matrix_status="DRY_RUN_BLOCKED",
            dry_run=True,
            official_data_mode="live",
            browser_runtime_mode="real",
        )
        == 3
    )


def test_validate_report_rejects_unknown_matrix_status_value() -> None:
    rep = build_live_validation_report(
        run_id="a",
        ruleset_version="1",
        rows=[_row(browser_status="PASS")],
        official_data_mode="live",
        browser_runtime_mode="real",
    )
    object.__setattr__(rep, "matrix_status", "WEIRD")
    with pytest.raises(ReportSchemaError):
        validate_report(rep)


def test_validate_report_rejects_unknown_execution_modes() -> None:
    rep = build_live_validation_report(
        run_id="a",
        ruleset_version="1",
        rows=[_row(browser_status="PASS")],
        official_data_mode="live",
        browser_runtime_mode="real",
    )
    object.__setattr__(rep, "official_data_mode", "mystery")
    with pytest.raises(ReportSchemaError):
        validate_report(rep)
    object.__setattr__(rep, "official_data_mode", "live")
    object.__setattr__(rep, "browser_runtime_mode", "mystery")
    with pytest.raises(ReportSchemaError):
        validate_report(rep)


def test_validate_report_rejects_inconsistent_row_rollup() -> None:
    rep = build_live_validation_report(
        run_id="a",
        ruleset_version="1",
        rows=[_row(browser_status="PASS")],
        official_data_mode="live",
        browser_runtime_mode="real",
    )
    bad_row = _row(browser_status="PASS", api_bucket="BUG", final_bucket="PASS")
    object.__setattr__(rep, "rows", (bad_row,))
    object.__setattr__(rep, "totals_by_bucket", {"PASS": 1, "FAIL": 0, "BLOCKED": 0})
    with pytest.raises(ReportSchemaError):
        validate_report(rep)


def test_validate_report_rejects_runtime_mode_row_status_mismatch() -> None:
    rep = build_live_validation_report(
        run_id="a",
        ruleset_version="1",
        rows=[_row(browser_status="STUB")],
        official_data_mode="recorded",
        browser_runtime_mode="stub",
    )
    validate_report(rep)
    object.__setattr__(rep, "browser_runtime_mode", "real")
    with pytest.raises(ReportSchemaError):
        validate_report(rep)


def test_validate_report_allows_none_browser_status_for_failed_real_rows() -> None:
    rep = build_live_validation_report(
        run_id="a",
        ruleset_version="1",
        rows=[_row(browser_status="NONE", api_bucket="BUG", final_bucket="FAIL")],
        official_data_mode="live",
        browser_runtime_mode="real",
    )
    validate_report(rep)


def test_validate_report_rejects_none_browser_status_for_passed_real_rows() -> None:
    rep = build_live_validation_report(
        run_id="a",
        ruleset_version="1",
        rows=[_row(browser_status="PASS")],
        official_data_mode="live",
        browser_runtime_mode="real",
    )
    object.__setattr__(rep, "rows", (_row(browser_status="NONE", final_bucket="PASS"),))
    object.__setattr__(rep, "totals_by_bucket", {"PASS": 1, "FAIL": 0, "BLOCKED": 0})
    with pytest.raises(ReportSchemaError):
        validate_report(rep)


def test_validate_report_rejects_totals_or_blocked_rows_mismatch() -> None:
    rep = build_live_validation_report(
        run_id="a",
        ruleset_version="1",
        rows=[_row(browser_status="BLOCKED", final_bucket="BLOCKED")],
        official_data_mode="live",
        browser_runtime_mode="real",
    )
    object.__setattr__(rep, "totals_by_bucket", {"PASS": 1, "FAIL": 0, "BLOCKED": 0})
    with pytest.raises(ReportSchemaError):
        validate_report(rep)
    object.__setattr__(rep, "totals_by_bucket", {"PASS": 0, "FAIL": 0, "BLOCKED": 1})
    object.__setattr__(rep, "blocked_rows", [])
    with pytest.raises(ReportSchemaError):
        validate_report(rep)
