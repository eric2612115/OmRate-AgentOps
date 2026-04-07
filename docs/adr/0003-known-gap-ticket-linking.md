# ADR 0003: Known-gap narrative in Git; Mongo tickets link by reference

## Title

Known-gap long-form narrative lives in Git; MongoDB tickets store only `known_gap_ref`, not duplicated full text.

## Status

Accepted

## Context

Known gaps are reviewed like code: prose, rationale, and acceptance criteria belong in version control with PR history. Tickets in MongoDB are useful for workflow, assignment, and cross-system queries. Duplicating the full gap narrative in both places causes drift, merge confusion, and unclear ownership of the canonical wording.

## Decision

- **Canonical long-form description** of a known gap (including context, scope, and remediation notes as maintained by this program) **lives in Git** under the agreed paths (for example `known-gaps/`).
- **MongoDB** (or other ticket store) records **only** a stable link via **`known_gap_ref`** (and minimal indexing fields if needed), pointing at the Git artifact (path + revision or equivalent), **without** copying the full narrative body into the ticket document.

## Consequences

- Positive: Single source of truth for gap text; tickets stay lightweight and consistent with repo history.
- Positive: Review and diff of gap content remain in Git tooling.
- Negative: Readers need Git (or rendered exports) to read full detail; ticket UI alone is insufficient for the canonical story.
- Operational: `known_gap_ref` scheme must be stable and documented; breaking renames require migration or redirects.
