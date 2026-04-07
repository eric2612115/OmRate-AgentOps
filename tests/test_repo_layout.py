"""Verify required control-plane repo layout files exist."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "README.md",
    "CLAUDE.md",
    ".cursor/rules/index.mdc",
    "pyproject.toml",
    ".env.example",
    "manifests/README.md",
    "protocol-rules/README.md",
    "known-gaps/README.md",
    "reports/README.md",
    "runbooks/protocol-run.md",
    "tests/test_repo_layout.py",
]

ADR_FILES = [
    "docs/adr/0001-runtime-run-coordinator.md",
    "docs/adr/0002-ruleset-propagation.md",
    "docs/adr/0003-known-gap-ticket-linking.md",
    "docs/adr/0004-validation-summary-boundary.md",
]


def test_required_files_exist():
    for rel in REQUIRED_FILES:
        path = REPO_ROOT / rel
        assert path.is_file(), f"Missing required file: {path}"


def test_adr_files_exist():
    for rel in ADR_FILES:
        path = REPO_ROOT / rel
        assert path.is_file(), f"Missing ADR file: {path}"
