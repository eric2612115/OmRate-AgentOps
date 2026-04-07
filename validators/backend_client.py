"""Synchronous HTTP client for OmRate backend validation admin APIs."""

from __future__ import annotations

from typing import Any

import httpx


class BackendValidationClient:
    """Calls `/admin/validation/*` and public `/markets` on a configurable API base."""

    def __init__(
        self,
        base_url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers or {},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> BackendValidationClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def create_protocol_run(self, payload: dict[str, Any]) -> Any:
        r = self._client.post("/admin/validation/protocol-runs", json=payload)
        r.raise_for_status()
        return r.json()

    def get_protocol_run(self, protocol_run_id: str) -> Any:
        r = self._client.get(f"/admin/validation/protocol-runs/{protocol_run_id}")
        r.raise_for_status()
        return r.json()

    def update_protocol_run(self, protocol_run_id: str, payload: dict[str, Any]) -> Any:
        r = self._client.post(
            "/admin/validation/protocol-runs",
            json={"protocol_run_id": protocol_run_id, **payload},
        )
        r.raise_for_status()
        return r.json()

    def list_protocol_runs(
        self,
        *,
        protocol: str | None = None,
        limit: int | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if protocol is not None:
            params["protocol"] = protocol
        if limit is not None:
            params["limit"] = limit
        r = self._client.get("/admin/validation/protocol-runs", params=params or None)
        r.raise_for_status()
        return r.json()

    def attach_validation_evidence(
        self,
        protocol_run_id: str,
        payload: dict[str, Any],
    ) -> Any:
        r = self._client.post(
            f"/admin/validation/protocol-runs/{protocol_run_id}/evidence",
            json=payload,
        )
        r.raise_for_status()
        return r.json()

    def list_validation_evidence(
        self,
        protocol_run_id: str,
        *,
        limit: int | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        r = self._client.get(
            f"/admin/validation/protocol-runs/{protocol_run_id}/evidence",
            params=params or None,
        )
        r.raise_for_status()
        return r.json()

    def list_market_tickets(
        self,
        market_id: str,
        *,
        limit: int | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        r = self._client.get(
            f"/admin/validation/markets/{market_id}/verification-tickets",
            params=params or None,
        )
        r.raise_for_status()
        return r.json()

    def list_market_events(
        self,
        market_id: str,
        *,
        limit: int | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        r = self._client.get(
            f"/admin/validation/markets/{market_id}/events",
            params=params or None,
        )
        r.raise_for_status()
        return r.json()

    def list_markets(
        self,
        *,
        protocol: str | None = None,
        limit: int | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if protocol is not None:
            params["protocol"] = protocol
        if limit is not None:
            params["limit"] = limit
        r = self._client.get("/markets", params=params or None)
        r.raise_for_status()
        return r.json()
