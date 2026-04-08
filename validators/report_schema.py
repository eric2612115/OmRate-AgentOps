"""Auditable JSON report contract for multi-protocol live validation matrix runs."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO

_MATRIX_STATUSES = frozenset({"PASS", "FAIL", "BLOCKED", "DRY_RUN_BLOCKED"})
_ROW_BUCKETS = frozenset({"PASS", "FAIL", "BLOCKED"})
_BROWSER_STATUSES = frozenset(
    {"PASS", "FAIL", "BLOCKED", "STUB", "NONE"},
)
_METRIC_BUCKETS = frozenset({"PASS", "WARN", "BUG"})
_OFFICIAL_DATA_MODES = frozenset({"live", "recorded"})
_BROWSER_RUNTIME_MODES = frozenset({"real", "stub", "off"})


class ReportSchemaError(ValueError):
    """Raised when a report or row violates the live-validation contract."""


@dataclass(frozen=True)
class LiveValidationRow:
    protocol: str
    chain_id: int
    market_id: str
    sample_rank: int
    expected_url: str | None
    resolved_url: str | None
    browser_status: str
    api_bucket: str
    url_bucket: str
    type_bucket: str
    final_bucket: str
    evidence_refs: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["evidence_refs"] = list(self.evidence_refs)
        return d

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> LiveValidationRow:
        ev = d.get("evidence_refs")
        if not isinstance(ev, list):
            raise ReportSchemaError("evidence_refs must be a list")
        return LiveValidationRow(
            protocol=str(d["protocol"]),
            chain_id=int(d["chain_id"]),
            market_id=str(d["market_id"]),
            sample_rank=int(d["sample_rank"]),
            expected_url=_optional_str(d.get("expected_url")),
            resolved_url=_optional_str(d.get("resolved_url")),
            browser_status=str(d["browser_status"]),
            api_bucket=str(d["api_bucket"]),
            url_bucket=str(d["url_bucket"]),
            type_bucket=str(d["type_bucket"]),
            final_bucket=str(d["final_bucket"]),
            evidence_refs=tuple(str(x) for x in ev),
        )


@dataclass(frozen=True)
class LiveValidationReport:
    run_id: str
    ruleset_version: str
    matrix_status: str
    official_data_mode: str
    browser_runtime_mode: str
    totals_by_bucket: dict[str, int]
    blocked_rows: list[dict[str, Any]]
    generated_at: str
    rows: tuple[LiveValidationRow, ...]
    dry_run: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "ruleset_version": self.ruleset_version,
            "matrix_status": self.matrix_status,
            "official_data_mode": self.official_data_mode,
            "browser_runtime_mode": self.browser_runtime_mode,
            "totals_by_bucket": dict(self.totals_by_bucket),
            "blocked_rows": list(self.blocked_rows),
            "generated_at": self.generated_at,
            "rows": [r.as_dict() for r in self.rows],
            "dry_run": self.dry_run,
        }

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> LiveValidationReport:
        rows_raw = d.get("rows")
        if not isinstance(rows_raw, list):
            raise ReportSchemaError("rows must be a list")
        rows = tuple(LiveValidationRow.from_dict(x) for x in rows_raw)
        br = d.get("blocked_rows")
        if not isinstance(br, list):
            raise ReportSchemaError("blocked_rows must be a list")
        totals = d.get("totals_by_bucket")
        if not isinstance(totals, dict):
            raise ReportSchemaError("totals_by_bucket must be a dict")
        return LiveValidationReport(
            run_id=str(d["run_id"]),
            ruleset_version=str(d["ruleset_version"]),
            matrix_status=str(d["matrix_status"]),
            official_data_mode=str(d["official_data_mode"]),
            browser_runtime_mode=str(d["browser_runtime_mode"]),
            totals_by_bucket={str(k): int(v) for k, v in totals.items()},
            blocked_rows=[dict(x) for x in br],
            generated_at=str(d["generated_at"]),
            rows=rows,
            dry_run=bool(d.get("dry_run", False)),
        )


def _optional_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def validate_row(row: LiveValidationRow) -> None:
    if not row.protocol.strip():
        raise ReportSchemaError("row.protocol is required")
    if not row.market_id.strip():
        raise ReportSchemaError("row.market_id is required")
    if row.sample_rank < 1:
        raise ReportSchemaError("row.sample_rank must be >= 1")
    if row.browser_status not in _BROWSER_STATUSES:
        raise ReportSchemaError(f"invalid browser_status: {row.browser_status!r}")
    if row.api_bucket not in _METRIC_BUCKETS:
        raise ReportSchemaError(f"invalid api_bucket: {row.api_bucket!r}")
    if row.url_bucket not in _METRIC_BUCKETS:
        raise ReportSchemaError(f"invalid url_bucket: {row.url_bucket!r}")
    if row.type_bucket not in _METRIC_BUCKETS:
        raise ReportSchemaError(f"invalid type_bucket: {row.type_bucket!r}")
    if row.final_bucket not in _ROW_BUCKETS:
        raise ReportSchemaError(f"invalid final_bucket: {row.final_bucket!r}")
    if not row.evidence_refs:
        raise ReportSchemaError("row.evidence_refs must be non-empty")


def _derived_final_bucket(row: LiveValidationRow) -> str:
    if row.browser_status == "BLOCKED":
        return "BLOCKED"
    if row.browser_status == "FAIL":
        return "FAIL"
    for bucket in (row.api_bucket, row.url_bucket, row.type_bucket):
        if bucket != "PASS":
            return "FAIL"
    return "PASS"


def _validate_browser_runtime_semantics(report: LiveValidationReport) -> None:
    allowed_by_mode = {
        "real": {"PASS", "FAIL", "BLOCKED", "NONE"},
        "stub": {"STUB"},
        "off": {"NONE"},
    }
    allowed = allowed_by_mode[report.browser_runtime_mode]
    for row in report.rows:
        if row.browser_status not in allowed:
            raise ReportSchemaError(
                "browser_runtime_mode "
                f"{report.browser_runtime_mode!r} is inconsistent with row.browser_status "
                f"{row.browser_status!r} for market_id={row.market_id!r}"
            )
        if report.browser_runtime_mode == "real" and row.browser_status == "NONE":
            if row.final_bucket != "FAIL":
                raise ReportSchemaError(
                    "browser_status 'NONE' is only allowed for failed rows in real mode "
                    f"(market_id={row.market_id!r})"
                )


def validate_report(report: LiveValidationReport) -> None:
    if not report.run_id.strip():
        raise ReportSchemaError("run_id is required")
    if not report.ruleset_version.strip():
        raise ReportSchemaError("ruleset_version is required")
    if report.matrix_status not in _MATRIX_STATUSES:
        raise ReportSchemaError(f"invalid matrix_status: {report.matrix_status!r}")
    if report.official_data_mode not in _OFFICIAL_DATA_MODES:
        raise ReportSchemaError(
            f"invalid official_data_mode: {report.official_data_mode!r}"
        )
    if report.browser_runtime_mode not in _BROWSER_RUNTIME_MODES:
        raise ReportSchemaError(
            f"invalid browser_runtime_mode: {report.browser_runtime_mode!r}"
        )
    if not report.generated_at.strip():
        raise ReportSchemaError("generated_at is required")
    for k in ("PASS", "FAIL", "BLOCKED"):
        if k not in report.totals_by_bucket:
            raise ReportSchemaError(f"totals_by_bucket missing key {k!r}")
    for row in report.rows:
        validate_row(row)
        derived_final = _derived_final_bucket(row)
        if row.final_bucket != derived_final:
            raise ReportSchemaError(
                f"row.final_bucket {row.final_bucket!r} != derived {derived_final!r} "
                f"for market_id={row.market_id!r}"
            )
    _validate_browser_runtime_semantics(report)
    expected_totals = _totals_from_rows(report.rows)
    if report.totals_by_bucket != expected_totals:
        raise ReportSchemaError(
            f"totals_by_bucket {report.totals_by_bucket!r} != derived {expected_totals!r}"
        )
    expected_blocked_rows = _blocked_rows_from_rows(report.rows)
    if report.blocked_rows != expected_blocked_rows:
        raise ReportSchemaError(
            f"blocked_rows {report.blocked_rows!r} != derived {expected_blocked_rows!r}"
        )
    if report.dry_run:
        if report.matrix_status != "DRY_RUN_BLOCKED":
            raise ReportSchemaError("dry_run report must have matrix_status DRY_RUN_BLOCKED")
    else:
        computed = matrix_status_from_rows(list(report.rows), dry_run=False)
        if computed != report.matrix_status:
            raise ReportSchemaError(
                f"matrix_status {report.matrix_status!r} != derived {computed!r}",
            )


def matrix_status_from_rows(rows: Sequence[LiveValidationRow], *, dry_run: bool = False) -> str:
    if dry_run:
        return "DRY_RUN_BLOCKED"
    if any(r.final_bucket == "BLOCKED" for r in rows):
        return "BLOCKED"
    if any(r.final_bucket == "FAIL" for r in rows):
        return "FAIL"
    return "PASS"


def _totals_from_rows(rows: Sequence[LiveValidationRow]) -> dict[str, int]:
    out = {"PASS": 0, "FAIL": 0, "BLOCKED": 0}
    for r in rows:
        out[r.final_bucket] = out.get(r.final_bucket, 0) + 1
    return out


def _blocked_rows_from_rows(rows: Sequence[LiveValidationRow]) -> list[dict[str, Any]]:
    return [
        {"protocol": r.protocol, "chain_id": r.chain_id, "market_id": r.market_id}
        for r in rows
        if r.final_bucket == "BLOCKED"
    ]


def _infer_browser_runtime_mode(
    rows: Sequence[LiveValidationRow],
    browser_runtime_mode: str,
) -> str:
    if browser_runtime_mode != "off":
        return browser_runtime_mode
    statuses = {row.browser_status for row in rows}
    if "STUB" in statuses:
        return "stub"
    if statuses - {"NONE"}:
        return "real"
    return "off"


def build_live_validation_report(
    *,
    run_id: str,
    ruleset_version: str,
    rows: Sequence[LiveValidationRow],
    official_data_mode: str = "recorded",
    browser_runtime_mode: str = "off",
    generated_at: str | None = None,
    dry_run: bool = False,
) -> LiveValidationReport:
    ts = generated_at
    if not ts:
        ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for row in rows:
        validate_row(row)
    totals = _totals_from_rows(rows)
    blocked = _blocked_rows_from_rows(rows)
    matrix = matrix_status_from_rows(rows, dry_run=dry_run)
    report = LiveValidationReport(
        run_id=run_id,
        ruleset_version=ruleset_version,
        matrix_status=matrix,
        official_data_mode=official_data_mode,
        browser_runtime_mode=_infer_browser_runtime_mode(rows, browser_runtime_mode),
        totals_by_bucket=totals,
        blocked_rows=blocked,
        generated_at=ts,
        rows=tuple(rows),
        dry_run=dry_run,
    )
    validate_report(report)
    return report


def dump_report(
    report: LiveValidationReport,
    path: str | Path | TextIO,
    *,
    indent: int = 2,
) -> None:
    payload = report.as_dict()
    text = json.dumps(payload, indent=indent, sort_keys=True) + "\n"
    if hasattr(path, "write"):
        cast_io = path  # type: ignore[assignment]
        cast_io.write(text)  # type: ignore[union-attr]
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def live_validation_matrix_exit_code(
    *,
    matrix_status: str,
    dry_run: bool,
    official_data_mode: str,
    browser_runtime_mode: str,
) -> int:
    """
    CLI exit mapping: dry-run is never pushable (3); FAIL/BLOCKED gate (1); PASS (0).

    ``matrix_status`` is ignored when ``dry_run`` is True (callers still pass the real status).
    """
    if dry_run:
        return 3
    if official_data_mode != "live" or browser_runtime_mode != "real":
        return 4
    if matrix_status in ("FAIL", "BLOCKED"):
        return 1
    return 0


def load_report(path: str | Path | TextIO) -> LiveValidationReport:
    if hasattr(path, "read"):
        raw = path.read()  # type: ignore[union-attr]
    else:
        raw = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ReportSchemaError(f"invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ReportSchemaError("report root must be an object")
    report = LiveValidationReport.from_dict(data)
    validate_report(report)
    return report


__all__ = [
    "LiveValidationReport",
    "LiveValidationRow",
    "ReportSchemaError",
    "build_live_validation_report",
    "dump_report",
    "live_validation_matrix_exit_code",
    "load_report",
    "matrix_status_from_rows",
    "validate_report",
    "validate_row",
]
