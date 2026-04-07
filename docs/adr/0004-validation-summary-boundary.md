# ADR 0004: `markets` validation summary boundary

## Title

`markets` may store only a lightweight validation summary; it must not hold ticket history, large evidence, adjudication notes, or full rules.

## Status

Accepted

## Context

The `markets` domain (or collection) is used for operational visibility and downstream consumers. If it absorbs unbounded narrative, binary blobs, full rule bodies, or adjudication threads, it becomes a second CMS, grows without bound, and blurs boundaries with Git-backed artifacts and backend-owned run records.

## Decision

- **`markets`** is limited to a **lightweight validation summary**: compact, structured fields sufficient for status, key identifiers, counts, timestamps, and short human-readable labels as defined by schema.
- The following are **out of scope** for `markets` and belong elsewhere (Git, blob storage, run records, or dedicated ticket/adjudication stores as per other ADRs):
  - **Ticket history** (event streams, comment threads)
  - **Large evidence** (logs, screenshots, big payloads)
  - **Adjudication notes** (extended rationale and debate)
  - **Full rules** (complete ruleset text or embedded rule documents)

## Consequences

- Positive: Predictable document size and query cost; clear data ownership.
- Positive: Forces heavy artifacts to use appropriate stores with retention and access controls.
- Negative: Consumers that want rich context must follow links to the correct systems rather than reading everything from `markets`.
- Operational: Schema reviews should reject fields that violate this boundary; violations are specification bugs, not ad-hoc exceptions.
