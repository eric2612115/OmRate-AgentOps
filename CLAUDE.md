# OmRate-AgentOps

## Read this first (index)

1. **This file (`CLAUDE.md`)** — repo purpose, boundaries, and where to look next.
2. **`README.md`** — human-facing overview of the control plane.
3. **`.cursor/rules/index.mdc`** — Cursor rule index for this repo.
4. **Directories**
   - `manifests/` — validation manifests
   - `protocol-rules/` — protocol and validation rules
   - `known-gaps/` — documented gaps and follow-ups
   - `reports/` — generated or checked-in reports
   - `runbooks/` — step-by-step operational runbooks
   - `tests/` — repository layout and future tests

## Purpose

OmRate-AgentOps is the **control plane** for validation orchestration, manifests, rules, and reports for the OmRate system.

## Sibling dependencies

Implementation and UI live in sibling repositories (not vendored here):

- `../OmRate-Backend` — APIs, auth, data, execution-facing backend
- `../OmRate-frontend` — browser UI and client integration
- `../Phalanx-Skill` — future action plane (wallet, signing, orchestration)

Use those paths when linking docs or automation that spans repos.

## Local configuration

Copy `.env.example` to `.env` and fill placeholders for your environment. Do not commit secrets.

## Live validation sync rules

If you change any of the following, update code, tests, and runbooks in the same change:

- `protocol-rules/official-sources.yaml`
- browser result semantics (`PASS` / `FAIL` / `BLOCKED`)
- report artifact schema in `validators/report_schema.py`
- matrix runner or push-gate CLI behavior
- operational prerequisites, stop conditions, or operator flow in `runbooks/`

Use `runbooks/session-prompt-live-validation.md` when resuming this workflow in a future session.

## Minimal verify

Run `python3 -m pytest tests/test_repo_layout.py -v` from the `OmRate-AgentOps` repo root.
