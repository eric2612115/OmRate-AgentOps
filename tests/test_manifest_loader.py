"""Manifest capture, YAML persistence, and backend client URL wiring."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from scripts.capture_manifest import capture_manifest
from validators.backend_client import BackendValidationClient
from validators.manifest_loader import load_manifest, save_manifest


def test_save_and_load_manifest_roundtrip(tmp_path: Path) -> None:
    manifest = {
        "ruleset_version": "ruleset-2026-04-07",
        "generated_at": "2026-04-07T12:00:00+00:00",
        "repos": [{"path": "/tmp/foo", "git_sha": "abc123"}],
    }
    path = tmp_path / "manifest.yaml"
    save_manifest(path, manifest)
    loaded = load_manifest(path)
    assert loaded == manifest


def test_load_manifest_rejects_non_mapping(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_manifest(path)


def test_capture_manifest_structure(tmp_path: Path) -> None:
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    repo_a.mkdir()
    repo_b.mkdir()
    (repo_a / "file.txt").write_text("x", encoding="utf-8")

    def fake_rev_parse(path: Path) -> str | None:
        if path.resolve() == repo_a.resolve():
            return "deadbeef1111"
        return None

    with patch("scripts.capture_manifest._git_head_sha", side_effect=fake_rev_parse):
        manifest = capture_manifest(
            repo_roots={"repo_a": str(repo_a), "repo_b": str(repo_b)},
            ruleset_version="v-test",
        )

    assert manifest["ruleset_version"] == "v-test"
    assert "generated_at" in manifest
    assert set(manifest["repos"]) == {"repo_a", "repo_b"}
    assert manifest["repos"]["repo_a"]["path"] == str(repo_a.resolve())
    assert manifest["repos"]["repo_b"]["path"] == str(repo_b.resolve())
    assert manifest["repos"]["repo_a"]["git_sha"] == "deadbeef1111"
    assert manifest["repos"]["repo_b"]["git_sha"] is None


def test_backend_validation_client_routes() -> None:
    captured: list[tuple[str, str, dict | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.method, str(request.url), dict(request.url.params)))
        return httpx.Response(200, json={"ok": True, "path": str(request.url.path)})

    transport = httpx.MockTransport(handler)
    client = BackendValidationClient("https://api.example.com", transport=transport)

    try:
        client.create_protocol_run({"protocol": "aave_v3"})
        client.get_protocol_run("run_abc")
        client.update_protocol_run(
            "run_abc",
            {
                "protocol": "morpho",
                "manifest_id": "m-1",
                "ruleset_version": "v1",
                "current_phase": "compare",
                "terminal_status": None,
                "actor_type": "agent",
            },
        )
        client.list_protocol_runs(protocol="morpho", limit=5)
        client.attach_validation_evidence("run_abc", {"kind": "browser"})
        client.list_validation_evidence("run_abc", limit=10)
        client.list_market_tickets("mkt_1", limit=3)
        client.list_market_events("mkt_1", limit=7)
        client.list_markets(protocol="aave_v3", limit=100)
    finally:
        client.close()

    methods_and_paths = [(m, httpx.URL(u).path) for m, u, _ in captured]
    assert methods_and_paths[0] == ("POST", "/admin/validation/protocol-runs")
    assert methods_and_paths[1] == ("GET", "/admin/validation/protocol-runs/run_abc")
    assert methods_and_paths[2] == ("POST", "/admin/validation/protocol-runs")
    assert methods_and_paths[3][0] == "GET"
    assert methods_and_paths[3][1] == "/admin/validation/protocol-runs"
    assert methods_and_paths[4] == (
        "POST",
        "/admin/validation/protocol-runs/run_abc/evidence",
    )
    assert methods_and_paths[5][1] == "/admin/validation/protocol-runs/run_abc/evidence"
    assert methods_and_paths[6][1] == "/admin/validation/markets/mkt_1/verification-tickets"
    assert methods_and_paths[7][1] == "/admin/validation/markets/mkt_1/events"
    assert methods_and_paths[8][1] == "/markets"

    params_by_path = {httpx.URL(u).path: dict(p) for _, u, p in captured}
    assert params_by_path["/admin/validation/protocol-runs"] == {
        "protocol": "morpho",
        "limit": "5",
    }
    assert params_by_path["/admin/validation/protocol-runs/run_abc/evidence"] == {
        "limit": "10",
    }
    assert params_by_path["/admin/validation/markets/mkt_1/verification-tickets"] == {"limit": "3"}
    assert params_by_path["/admin/validation/markets/mkt_1/events"] == {"limit": "7"}
    assert params_by_path["/markets"] == {"protocol": "aave_v3", "limit": "100"}


def test_backend_client_json_body_on_create() -> None:
    bodies: list[bytes | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        return httpx.Response(201, json={"protocol_run_id": "run_x"})

    transport = httpx.MockTransport(handler)
    client = BackendValidationClient("https://h.test", transport=transport)
    try:
        out = client.create_protocol_run({"protocol": "lista"})
        assert out["protocol_run_id"] == "run_x"
    finally:
        client.close()
    assert json.loads(bodies[0].decode()) == {"protocol": "lista"}
