# OmRate-AgentOps

Control-plane repository for **OmRate validation orchestration**: manifests, protocol rules, known gaps, and reports. This repo coordinates validation workflows and artifacts; it does not replace application code in sibling repos.

## Scope

- **Manifests**: validation and deployment intent for agent/ops flows
- **Protocol rules**: constraints and checklists used during validation
- **Reports**: outputs and summaries from orchestrated runs
- **Runbooks**: operational steps (for example, protocol execution)
- **ADRs**: validation architecture decisions in [`docs/adr/`](docs/adr/)

## Related repositories

This repo is designed to sit alongside:

- `../OmRate-Backend`
- `../OmRate-frontend`
- `../Phalanx-Skill`

See `CLAUDE.md` for agent and editor navigation.
