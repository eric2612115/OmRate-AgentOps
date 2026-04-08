#!/usr/bin/env python3
"""Mechanical push gate: accept only a valid live-validation report artifact with full PASS."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from validators.report_schema import ReportSchemaError, load_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Require live-validation report: non-dry-run, matrix PASS, all rows PASS.",
    )
    parser.add_argument(
        "report_path",
        type=Path,
        help="Path to live validation report JSON",
    )
    args = parser.parse_args(argv)

    try:
        report = load_report(args.report_path)
    except OSError as e:
        print(f"gate: cannot read report: {e}", file=sys.stderr)
        return 2
    except ReportSchemaError as e:
        print(f"gate: invalid report: {e}", file=sys.stderr)
        return 2

    if report.dry_run:
        print(
            f"gate: rejected dry_run=True matrix_status={report.matrix_status!r}",
            file=sys.stderr,
        )
        return 4
    if report.official_data_mode != "live":
        print(
            f"gate: rejected official_data_mode={report.official_data_mode!r} (required 'live')",
            file=sys.stderr,
        )
        return 4
    if report.browser_runtime_mode != "real":
        print(
            f"gate: rejected browser_runtime_mode={report.browser_runtime_mode!r} (required 'real')",
            file=sys.stderr,
        )
        return 4
    if not report.rows:
        print("gate: rejected empty matrix report (requires at least one sampled row)", file=sys.stderr)
        return 4
    if report.matrix_status == "BLOCKED":
        print("gate: matrix_status='BLOCKED' (requires PASS)", file=sys.stderr)
        return 3
    if report.matrix_status != "PASS":
        print(f"gate: matrix_status={report.matrix_status!r} (required PASS)", file=sys.stderr)
        return 1

    bad = [r for r in report.rows if r.final_bucket != "PASS"]
    if bad:
        print(
            f"gate: {len(bad)} row(s) with final_bucket != PASS "
            f"(sample: market_id={bad[0].market_id!r} final_bucket={bad[0].final_bucket!r})",
            file=sys.stderr,
        )
        return 3 if bad[0].final_bucket == "BLOCKED" else 1

    print(
        f"gate: OK run_id={report.run_id} ruleset={report.ruleset_version} "
        f"rows={len(report.rows)} matrix_status=PASS",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
