# Protocol run

Operational runbook for executing a **protocol validation** flow from this control plane.

## Prerequisites

- Environment variables from `.env.example` copied to `.env` and filled with non-production placeholders or team-approved values.
- Access to `../OmRate-Backend` as required by the manifest for the run.

## Steps (skeleton)

1. Confirm `RULESET_VERSION` and manifest revision for this run.
2. Load manifests from `manifests/` and rules from `protocol-rules/`.
3. Execute orchestration steps (tooling TBD); record outcomes under `reports/`.
4. File new items in `known-gaps/` if blockers or ambiguities appear.

## Rollback / abort

Stop the run if credentials or endpoints are wrong; do not commit secrets or real production URLs into this repo.
