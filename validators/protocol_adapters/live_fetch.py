"""Shared helpers for live official-source fetches used by protocol adapters."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

import httpx
from web3 import Web3

from validators.retry_policy import request_json_with_retry

DEFAULT_HTTP_TIMEOUT_SECONDS = 30.0
DEFAULT_RPC_TIMEOUT_SECONDS = 15.0
DEFAULT_RPC_URLS: dict[int, str] = {
    1: "https://ethereum-rpc.publicnode.com",
    10: "https://optimism-rpc.publicnode.com",
    56: "https://bsc-dataseed.binance.org",
    100: "https://rpc.gnosis.gateway.fm",
    137: "https://polygon.drpc.org",
    146: "https://rpc.soniclabs.com",
    8453: "https://mainnet.base.org",
    42161: "https://arb1.arbitrum.io/rpc",
    43114: "https://api.avax.network/ext/bc/C/rpc",
    5000: "https://rpc.mantle.xyz",
    534352: "https://rpc.scroll.io",
}


@contextmanager
def managed_http_client(transport: Any = None) -> Iterator[httpx.Client]:
    if isinstance(transport, httpx.Client):
        yield transport
        return
    timeout = float(os.environ.get("HTTPX_TIMEOUT_SECONDS", DEFAULT_HTTP_TIMEOUT_SECONDS))
    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"user-agent": "OmRate-AgentOps/0.0.0"},
        transport=transport if isinstance(transport, httpx.BaseTransport) else None,
    ) as client:
        yield client


def post_graphql_json(
    client: httpx.Client,
    url: str,
    *,
    query: str,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = request_json_with_retry(
        client,
        "POST",
        url,
        json={"query": query, "variables": variables or {}},
    )
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("graphql response root must be an object")
    if payload.get("errors"):
        raise ValueError(f"graphql returned errors: {payload['errors']!r}")
    return payload


def get_json(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = request_json_with_retry(client, "GET", url, params=params)
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("json response root must be an object")
    return payload


def post_json(
    client: httpx.Client,
    url: str,
    *,
    body: dict[str, Any],
) -> dict[str, Any]:
    response = request_json_with_retry(client, "POST", url, json=body)
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("json response root must be an object")
    return payload


def rpc_url_for_chain(chain_id: int) -> str:
    env_value = os.environ.get(f"RPC_{chain_id}", "").strip()
    if env_value:
        return env_value
    try:
        return DEFAULT_RPC_URLS[chain_id]
    except KeyError as exc:
        raise KeyError(f"missing RPC url for chain_id={chain_id}") from exc


def make_web3(chain_id: int) -> Web3:
    timeout = float(os.environ.get("RPC_TIMEOUT_SECONDS", DEFAULT_RPC_TIMEOUT_SECONDS))
    provider = Web3.HTTPProvider(
        rpc_url_for_chain(chain_id),
        request_kwargs={"timeout": timeout},
    )
    return Web3(provider)

