# Live validation

DefiLlama is used only to cross-check that official website and app hosts match the expected protocol identity; it is **not** a market data truth source.

Pinned official website, app host, API base, and market-page URL templates live in `protocol-rules/official-sources.yaml`; update that file first when a protocol changes.

## Prerequisites

- `OMRATE_BACKEND_BASE_URL` points at the backend under validation.
- `RULESET_VERSION` is set for the current validation run.
- `OMRATE_INTERNAL_API_SECRET` is set when admin routes require the internal header.
- Recorded official payloads are available as a JSON map for `scripts/run_live_validation_matrix.py --recorded-payloads`.

## Core commands

Run the sampled matrix and emit a report:

```bash
python3 scripts/run_live_validation_matrix.py --recorded-payloads path/to/official-payloads.json
```

Run a non-push dry run:

```bash
python3 scripts/run_live_validation_matrix.py --dry-run --recorded-payloads path/to/official-payloads.json
```

Evaluate push readiness from a produced report:

```bash
python3 scripts/check_backend_push_gate.py reports/live-validation/matrix_YYYYMMDDTHHMMSSZ.json
```

## Stop conditions

Stop and treat the run as blocked when any of the following is true:

- browser validation returns `BLOCKED`
- report `matrix_status` is not `PASS`
- any sampled row has `final_bucket != PASS`
- the official source registry or market-page template is stale and needs a human update
- required credentials, internal headers, or recorded/live official payload sources are missing
