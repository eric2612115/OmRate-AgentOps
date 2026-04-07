"""Build a validation manifest describing local repo roots and ruleset version."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _git_head_sha(repo_root: Path) -> str | None:
    try:
        cp = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return cp.stdout.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None


def capture_manifest(
    repo_roots: dict[str, str],
    ruleset_version: str,
) -> dict[str, Any]:
    """Return a manifest dict with ruleset_version, generated_at (UTC ISO), and repos."""
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    repos: dict[str, dict[str, Any]] = {}
    for repo_name, root in repo_roots.items():
        path = Path(root).expanduser().resolve()
        repos[repo_name] = {
            "path": str(path),
            "git_sha": _git_head_sha(path),
        }
    return {
        "ruleset_version": ruleset_version,
        "generated_at": generated_at,
        "repos": repos,
    }
