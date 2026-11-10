# ADR 0001: Modular monolith with a pure domain core

- Status: accepted
- Date: 2026-11-09

## Context

The product's value is correctness under change: impact analysis, readiness gating, and approvals bound to an exact
baseline. Those guarantees need **single-transaction** updates across BOM revisions, artifacts, exceptions,
approvals and audit (spec invariant I-2). The team is one engineer plus coding agents. The only workload that
needs separate scaling is document extraction, which is slow, retryable and may call a paid model.

## Decision

- One FastAPI service and one Postgres database. Modules are separated by package boundaries:
  - `app/domain/*` holds pure functions: BOM diff and hash, graph, impact, rules, artifacts and readiness. It has
    no DB, I/O or framework imports.
  - `app/services/*` holds the transactions, locking, audit and outbox.
  - `app/api/v1/*` holds HTTP and authorization.
- A single Celery worker handles asynchronous extraction and outbox dispatch. Work reaches it through a
  transactional outbox, never by publishing to Redis inside a request.
- Derived state (readiness) is recomputed under the project row lock from persisted facts and is never set
  directly.

## Consequences

- Positive:
  - Atomic publish (revision + invalidations + exceptions + stale approvals) is a single SQL transaction.
  - The domain core is fast to test without infrastructure: the unit tests and Hypothesis properties run with no
    DB.
  - Deployment is two processes and two managed stores.
- Negative:
  - Scaling is coarse. The API scales horizontally, but the worker currently embeds beat and must stay a
    singleton.
  - Module boundaries are enforced by review and ownership (`PARALLEL_AGENT_HANDOFF.md`), not by a tool. An
    import-linter rule would be a cheap next step.
- Revisit if extraction volume or tenant isolation requires separate services. The outbox events are the natural
  seam.
