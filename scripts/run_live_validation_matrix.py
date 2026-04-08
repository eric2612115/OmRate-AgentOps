#!/usr/bin/env python3
"""Multi-protocol live validation matrix: sample markets, compare API/URL/type, write JSON report."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from validators.backend_client import BackendValidationClient
from validators.browser_validation import (
    BrowserValidationAdapter,
    EchoStubBrowserAdapter,
    PlaywrightBrowserInspector,
    RealBrowserValidationAdapter,
)
from validators.protocol_adapters import list_registered_protocol_ids
from validators.protocol_api_compare import run_live_validation_matrix
from validators.report_schema import dump_report, live_validation_matrix_exit_code


def _load_payload_table(path: Path) -> dict[str, dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("recorded payload fixture root must be an object")
    table: dict[str, dict] = {}
    for protocol, payload in raw.items():
        if not isinstance(protocol, str) or not isinstance(payload, dict):
            raise ValueError("recorded payload fixtures must map protocol id to object payload")
        table[protocol] = payload
    return table


def _official_payload_provider(table: dict[str, dict], protocol: str, market: dict) -> dict:
    payload = table.get(protocol)
    if payload is None:
        raise KeyError(f"no recorded smoke payload for protocol {protocol!r}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run multi-protocol live validation matrix and emit a JSON report.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mark report as non-pushable (DRY_RUN_BLOCKED) and always exit non-zero.",
    )
    parser.add_argument(
        "--protocol",
        action="append",
        dest="protocols",
        metavar="ID",
        help="Restrict to protocol id (repeatable). Default: all registered adapters.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Report JSON path (default: reports/live-validation/matrix_<ts>.json).",
    )
    parser.add_argument("--limit", type=int, default=50, help="Max markets per GET /markets")
    parser.add_argument(
        "--per-chain-limit",
        type=int,
        default=5,
        help="Max sampled markets per chain (default: 5)",
    )
    parser.add_argument(
        "--recorded-payloads",
        type=Path,
        default=None,
        help="JSON file mapping protocol id -> recorded official payload. Optional debug/fallback path only; not release-ready.",
    )
    parser.add_argument(
        "--browser-mode",
        choices=("off", "stub", "real"),
        default="off",
        help="Browser validation mode. Only 'real' is release-ready.",
    )
    args = parser.parse_args(argv)

    base = os.environ.get("OMRATE_BACKEND_BASE_URL", "").strip()
    if not base:
        sys.stderr.write("OMRATE_BACKEND_BASE_URL is required\n")
        return 2

    ruleset = os.environ.get("RULESET_VERSION", "0.0.0").strip() or "0.0.0"
    headers: dict[str, str] = {}
    internal_secret = os.environ.get("OMRATE_INTERNAL_API_SECRET", "").strip()
    if internal_secret:
        headers["x-internal-secret"] = internal_secret

    protocols = args.protocols
    if protocols is None:
        protocols = list(list_registered_protocol_ids())

    run_id = f"matrix_{uuid.uuid4().hex[:16]}"
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.output
    if out is None:
        out = _REPO_ROOT / "reports" / "live-validation" / f"matrix_{ts}.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    payload_table: dict[str, dict] | None = None
    if args.recorded_payloads is not None:
        try:
            payload_table = _load_payload_table(args.recorded_payloads)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            sys.stderr.write(f"failed to load recorded payloads: {exc}\n")
            return 2

    official_data_mode = "recorded" if payload_table is not None else "live"

    browser_adapter: BrowserValidationAdapter | None = None
    if args.browser_mode == "stub":
        browser_adapter = EchoStubBrowserAdapter()
    elif args.browser_mode == "real":
        browser_artifacts_dir = (
            _REPO_ROOT / "reports" / "live-validation" / "browser-artifacts" / run_id
        )
        browser_adapter = RealBrowserValidationAdapter(
            PlaywrightBrowserInspector(
                headless=os.environ.get("PLAYWRIGHT_HEADLESS", "0").strip() != "0",
                timeout_ms=int(os.environ.get("PLAYWRIGHT_TIMEOUT_MS", "20000").strip()),
                wait_after_load_ms=int(
                    os.environ.get("PLAYWRIGHT_WAIT_AFTER_LOAD_MS", "3000").strip()
                ),
                artifacts_dir=browser_artifacts_dir,
            )
        )

    client = BackendValidationClient(base, headers=headers or None)
    try:
        report = run_live_validation_matrix(
            client,
            run_id=run_id,
            ruleset_version=ruleset,
            official_data_mode=official_data_mode,
            browser_runtime_mode=args.browser_mode,
            protocols=protocols,
            limit=args.limit,
            per_chain_limit=args.per_chain_limit,
            official_payload_provider=(
                (lambda protocol, market: _official_payload_provider(payload_table, protocol, market))
                if payload_table is not None
                else None
            ),
            browser_adapter=browser_adapter,
            dry_run=args.dry_run,
            progress_log=lambda message: print(message, flush=True),
        )
    finally:
        client.close()

    dump_report(report, out)
    print(out)

    return live_validation_matrix_exit_code(
        matrix_status=report.matrix_status,
        dry_run=args.dry_run,
        official_data_mode=report.official_data_mode,
        browser_runtime_mode=report.browser_runtime_mode,
    )


if __name__ == "__main__":
    raise SystemExit(main())
