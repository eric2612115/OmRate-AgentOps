# Live validation matrix reports

This directory is the active path for live-validation report outputs.

JSON artifacts emitted by `scripts/run_live_validation_matrix.py` are written here by default
(`matrix_<UTC-timestamp>.json` unless `--output` is set).

Historical checked outputs should be moved to `reports/archive/live-validation/` once they are
no longer the current source of truth for an active workflow.

Each file validates against the schema implemented in `validators/report_schema.py`
(`LiveValidationReport` / `LiveValidationRow`). Use `load_report()` to parse and enforce
the contract after transport or manual edits.

Top-level fields include `run_id`, `ruleset_version`, `matrix_status`, `totals_by_bucket`,
`blocked_rows`, `generated_at`, and `rows`. Row-level buckets use API comparator vocabulary
(`PASS` / `WARN` / `BUG`) for `api_bucket`, `url_bucket`, and `type_bucket`; `final_bucket`
collapses to `PASS` / `FAIL` / `BLOCKED` for release gating.

`matrix_status` is `DRY_RUN_BLOCKED` when `--dry-run` is used; that state is never pushable,
and the CLI exits with a dedicated non-zero code even if every row would otherwise pass.
