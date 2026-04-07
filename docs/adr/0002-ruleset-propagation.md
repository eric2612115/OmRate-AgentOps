# ADR 0002: Ruleset authoring authority and runtime reference

## Title

Git is the ruleset authoring authority; runtime does not depend on live Git; backend uses the last known active ruleset reference.

## Status

Accepted

## Context

Protocol rules evolve in version-controlled artifacts. Production validation and enforcement must remain stable when Git is unavailable, when CI checks out a snapshot, or when operators run from cached or bundled material. Tying runtime behavior to a live Git remote would create availability coupling and non-reproducible runs.

## Decision

- **Git** (this repo and its review workflow) is the **authoring authority** for the protocol ruleset: human-readable source of truth, review, and history.
- **Runtime** paths **do not** require **live Git** access to resolve or apply rules; they operate on **materialized** or **previously resolved** rule content and references as supplied by deployment and backend.
- **OmRate-Backend** uses the **last known active ruleset reference** (for example commit SHA, content hash, or manifest pointer agreed with AgentOps) when executing validation logic, so behavior is pinned and recoverable independent of Git uptime.

## Consequences

- Positive: Reproducible validation aligned to a specific ruleset revision; no hard dependency on Git at request time.
- Positive: Clear separation between “what we edited” (Git) and “what the system runs” (backend-held reference + artifacts).
- Negative: Propagation from Git to “active” reference is an explicit operational step; stale references must be detected and updated deliberately.
- Operational: Document how the active reference is promoted and verified in runbooks and manifests.
