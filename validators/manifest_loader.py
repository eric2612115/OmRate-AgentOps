"""Load and save AgentOps manifest documents as YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def save_manifest(path: str | Path, manifest: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        manifest,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    p.write_text(text, encoding="utf-8")


def load_manifest(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("manifest root must be a YAML mapping (dict)")
    return raw
