#!/usr/bin/env python3
"""Minimal CLI: protocol validation run using backend client and mirror official snapshot."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from validators.backend_client import BackendValidationClient
from validators.browser_validation import BrowserValidationAdapter, EchoStubBrowserAdapter
from validators.protocol_api_compare import run_protocol_validation


def _mirror_official_from_backend(market: dict) -> dict:
    """Smoke default: treat backend as ground truth so wiring can run without external APIs."""
    return {
        "supply_apy": market.get("supply_apy"),
        "borrow_apy": market.get("borrow_apy"),
        "net_supply_apy": market.get("net_supply_apy"),
        "net_borrow_apy": market.get("net_borrow_apy"),
        "expected_market_url": market.get("market_url") or "",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run protocol API/url validation against OmRate backend.")
    parser.add_argument("--protocol", required=True, help="Protocol id filter, e.g. aave_v3")
    parser.add_argument("--limit", type=int, default=50, help="Max markets from GET /markets")
    parser.add_argument(
        "--per-chain-limit",
        type=int,
        default=5,
        help="Max markets per chain after deterministic sampling (default: 5)",
    )
    parser.add_argument(
        "--manifest-path",
        type=Path,
        default=None,
        help="Where to write the run manifest YAML (default: reports/protocol_run_<protocol>_<ts>.yaml)",
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
    manifest_path = args.manifest_path
    if manifest_path is None:
        reports = Path("reports")
        reports.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        manifest_path = reports / f"protocol_run_{args.protocol}_{ts}.yaml"

    browser_adapter: BrowserValidationAdapter | None = None
    if os.environ.get("OMRATE_BROWSER_VALIDATION", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        browser_adapter = EchoStubBrowserAdapter()

    client = BackendValidationClient(base, headers=headers or None)
    try:
        summary = run_protocol_validation(
            client,
            protocol=args.protocol,
            limit=args.limit,
            per_chain_limit=args.per_chain_limit,
            ruleset_version=ruleset,
            manifest_path=manifest_path,
            official_for_market=_mirror_official_from_backend,
            browser_adapter=browser_adapter,
        )
    finally:
        client.close()

    print(summary)
    return 0 if summary.get("terminal_status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
