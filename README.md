# OmRate-AgentOps

Control-plane repository for **OmRate validation orchestration**: manifests, protocol rules, known gaps, and reports. This repo coordinates validation workflows and artifacts; it does not replace application code in sibling repos.

## Scope

- **Manifests**: validation and deployment intent for agent/ops flows
- **Protocol rules**: constraints and checklists used during validation
- **Reports**: outputs and summaries from orchestrated runs
- **Runbooks**: operational steps (for example, protocol execution)
- **ADRs**: validation architecture decisions in [`docs/adr/`](docs/adr/)

## Live Validation Flow

The current live-validation control plane is built around these pinned artifacts:

- `protocol-rules/official-sources.yaml`: official websites, app hosts, API bases, and market-page URL templates
- `scripts/run_live_validation_matrix.py`: sampled multi-protocol matrix runner
- `scripts/check_backend_push_gate.py`: strict PASS-only gate for backend push decisions
- `reports/live-validation/`: JSON report artifacts consumed by the gate

If any validation flow, source registry, browser outcome rules, evidence shape, report schema, or gate criteria changes, update the matching code, tests, and runbooks in this repo in the same change.

## Related repositories

This repo is designed to sit alongside:

- `../OmRate-Backend`
- `../OmRate-frontend`
- `../Phalanx-Skill`

See `CLAUDE.md` for agent and editor navigation.
