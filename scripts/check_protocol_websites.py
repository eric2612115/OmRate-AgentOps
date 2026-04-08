#!/usr/bin/env python3
"""Print official website / app metadata for a protocol (trusted source registry)."""

from __future__ import annotations

import argparse
import json
import sys

from validators.official_sources import (
    PROTOCOL_IDS,
    UnknownProtocolError,
    build_protocol_website_check,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Output trusted website/app host metadata for live validation plumbing.",
    )
    parser.add_argument(
        "--protocol",
        required=True,
        choices=sorted(PROTOCOL_IDS),
        help="Protocol id from the trusted registry",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit one JSON object on stdout",
    )
    args = parser.parse_args(argv)

    try:
        meta = build_protocol_website_check(args.protocol)
    except UnknownProtocolError as e:
        print(str(e), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(meta, indent=2))
    else:
        print(f"protocol_id: {meta['protocol_id']}")
        print(f"official_website_url: {meta['official_website_url']}")
        print(f"trusted_app_hosts: {', '.join(meta['trusted_app_hosts'])}")
        dl = meta["defillama"]
        print(f"defillama.slug: {dl['slug']}")
        print(f"defillama.protocol_page_url: {dl['protocol_page_url']}")
        print(f"official_api_base: {meta['official_api_base']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
