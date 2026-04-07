# ADR 0001: Runtime run coordinator and phase authority

## Title

Backend owns `protocol_run_record` runtime authority; AgentOps advances run phase only through internal API.

## Status

Accepted

## Context

Validation orchestration spans AgentOps (control-plane artifacts, manifests, runbooks) and OmRate-Backend (durable run state, enforcement, and integration with execution surfaces). Without a single runtime authority, phase transitions could be duplicated or conflict between repos, tools, or ad-hoc scripts.

## Decision

- **OmRate-Backend** is the **runtime authority** for `protocol_run_record`: it owns creation, persistence, invariants, and authoritative phase transitions for a run.
- **OmRate-AgentOps** does **not** mutate run state directly against the database or bypass backend rules. It **only** advances (or requests advancement of) run **phase** through a defined **internal API** exposed or consumed by the backend, so all transitions remain auditable and consistent.

## Consequences

- Positive: One source of truth for run lifecycle; easier auditing and fewer split-brain phase states.
- Positive: AgentOps stays focused on authoring and coordination artifacts rather than embedding runtime policy.
- Negative: AgentOps automation must integrate with the internal API contract (versioning and auth are backend concerns).
- Operational: Changes to phase semantics or allowed transitions require backend coordination; ADR updates if the boundary shifts.
