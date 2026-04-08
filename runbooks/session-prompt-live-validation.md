# Live Validation Session Prompt

Use this prompt when resuming OmRate live-validation work in a future session.

## Goal

Resume the OmRate live-validation control plane and continue from the current repository state without re-planning from scratch.

## Required context

- Read `CLAUDE.md`, `README.md`, and `runbooks/live-validation.md` first.
- Treat `protocol-rules/official-sources.yaml` as the pinned local source for official websites, app hosts, API bases, and market-page URL templates.
- Treat `validators/report_schema.py`, `scripts/run_live_validation_matrix.py`, and `scripts/check_backend_push_gate.py` as the current release-gate contract.
- Keep `OmRate-AgentOps` and backend contract assumptions in sync.

## Preconditions

- Backend target is reachable through `OMRATE_BACKEND_BASE_URL`.
- `RULESET_VERSION` is set.
- `OMRATE_INTERNAL_API_SECRET` is available when required.
- Official payload source is available:
  - currently `scripts/run_live_validation_matrix.py` expects `--recorded-payloads <file.json>`
  - if live payload fetch is implemented later, update this prompt and `runbooks/live-validation.md` together

## Standard commands

Verify AgentOps tests:

```bash
./.venv/bin/pytest tests -v
```

Run backend contract tests from the backend repo/worktree:

```bash
./.venv/bin/pytest tests/test_live_validation_contracts.py -v
```

Run the sampled matrix:

```bash
python3 scripts/run_live_validation_matrix.py --recorded-payloads path/to/official-payloads.json
```

Run the dry-run gate rehearsal:

```bash
python3 scripts/run_live_validation_matrix.py --dry-run --recorded-payloads path/to/official-payloads.json
```

Evaluate push readiness:

```bash
python3 scripts/check_backend_push_gate.py reports/live-validation/<report>.json
```

## Non-negotiable stop conditions

Stop and report instead of forcing progress if:

- any sampled row is `FAIL` or `BLOCKED`
- browser validation is blocked by login, captcha, anti-bot, geo restriction, or site outage
- official URLs / hosts / templates in `protocol-rules/official-sources.yaml` appear stale
- report schema, matrix runner behavior, or push-gate rules changed without matching docs/tests updates
- backend `/markets` contract no longer matches matrix-runner assumptions

## Sync requirements

If you change any of these, update code, tests, and runbooks in the same change:

- `protocol-rules/official-sources.yaml`
- browser outcome semantics
- report schema
- matrix CLI
- push-gate CLI
- backend contract assumptions
