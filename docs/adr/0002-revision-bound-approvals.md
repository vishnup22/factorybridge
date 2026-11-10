# ADR 0002: Approvals are bound to a baseline hash and version

- Status: accepted
- Date: 2026-11-09

## Context

The core failure the product prevents is an approval given for one design that is still treated as valid after the
design changes. A boolean "approved" flag, or an approval tied only to a project, can't detect that. Races matter
too: an approver can click Approve while an engineer publishes a change (AT-07, AT-08).

## Decision

- The **baseline** is a canonical sha256 over every readiness input: the current BOM revision lines, the current
  artifact revisions, active dependency edges, compatibility rules, and the rule-engine version (`domain/baseline.py`,
  inputs gathered in `services/state.py::baseline_inputs`). `baseline_version`
  is a monotonic counter on the project.
- An approval request records `baseline_hash` and `baseline_version` when it is requested. The decision must send
  `expected_baseline_hash`, and is rejected with `409 APPROVAL_STALE` / `REVIEWED_BASELINE_MISMATCH` if the hash
  doesn't match the current one.
- Every baseline-changing transaction calls `invalidate_baseline`: it increments the version and marks
  pending/approved approvals `STALE`, inside the same transaction and under the project `FOR UPDATE` lock.
- Readiness is `READY` only if exactly one APPROVED, non-stale approval matches the current hash and version and no
  open HIGH/CRITICAL exceptions remain. That evaluation is a pure function (`domain/readiness.py`).
- Separation of duties: the decider must be the assigned, eligible approver. They must not be the requester, or
  anyone who submitted or published a change since the last approved revision (`SEPARATION_OF_DUTIES`).

## Consequences

- A stale approval can't make a project READY, whatever the request order. Six forced interleavings in
  `test_reliability.py` check this, along with a concurrent-decision test asserting the invariant.
- Old approvals are kept, marked `STALE`, so the audit trail shows exactly what was approved and when it stopped
  being valid.
- Any new readiness input must be added to the baseline hash. If it's missed, a change to that input won't
  invalidate approvals. Reviewers should check this on every PR that touches `domain/baseline.py`.
