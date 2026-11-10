# Implementation status

Last updated: 2026-11-09. Every "done" item below was exercised by a command whose result is in
[`TEST_RESULTS.md`](TEST_RESULTS.md). "Not done" means not built or not verified — nothing here is aspirational.

## Summary

| Phase (spec §14) | Status |
|---|---|
| 0 — Foundation (compose stack, config, health, logging, lint, CI) | Done locally. **First GitHub CI run failed** (MinIO image no longer pullable); fix ready in the working tree, not pushed. |
| 1 — Domain model + deterministic demo | Done |
| 2 — Approval integrity + reliability | Done |
| 3 — Frontend demo | Done; full flagship flow verified in Chromium via Playwright |
| 4 — Document intake and AI | Done for fixture mode. Live Anthropic adapter implemented and unit-tested with mocks; **never run against the real API** (no key available). |
| 5 — Deploy + harden | Deployment configs written and validated locally; **not deployed** — needs accounts/credentials (see `DEPLOYMENT_GUIDE.md`). |

## Acceptance tests (spec §12)

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| AT-01 | R1 only from reviewed extraction or validated CSV | Pass | `test_acceptance.py::test_at01_*`, `test_flagship.py` |
| AT-02 | R2 preserves R1; correct diff/hash | Pass | `test_acceptance.py::test_at02_*`, `test_immutability_enforced_in_database` |
| AT-03 | ACT-200 change impacts supplier, compatibility, WI, QC, approval | Pass | `test_at03_*` against `demo/fixtures/expected_impact.json`; Playwright asserts the same nodes in the UI |
| AT-04 | Missing evidence / unknown compatibility / unreviewed critical issues block | Pass | `test_at04_*` |
| AT-05 | Customer approval → 403 | Pass | `test_at05_*`, `demo_flow.py` |
| AT-06 | Change author approving own change → 403 | Pass | `test_at06_*` |
| AT-07 | Stale approval after R2 → 409, BLOCKED remains | Pass | `test_at07_*`, `demo_flow.py`, Playwright (stale banner) |
| AT-08 | Approve/publish race never leaves READY on stale baseline | Pass | `test_reliability.py` — 6 thread interleavings + concurrent publish/decision |
| AT-09 | Worker death/replay: no duplicate issues or revisions | Pass | `test_reliability.py` — simulated crashes, lease expiry, replay, zombie worker |
| AT-10 | Idempotency key replay / changed body → 409 | Pass | `test_at10_*` |
| AT-11 | Malformed PDF never becomes fabricated extraction | Pass | `test_at11_*` (malformed, image-only, disguised, path traversal) |
| AT-12 | End-to-end UI demo, fixture mode (+ optional live LLM) | **Fixture mode: pass** (Playwright, 3 runs incl. one on the final rebuilt images). **Live LLM: not run** (no API key). | `frontend/e2e/flagship.spec.ts` |
| AT-13 | Audit reconstructs change/approval events | Pass | `test_flagship.py` (ordered `seq`, action subsequence) |
| AT-14 | Production uses env secrets; API + UI over HTTPS | **Partially verified.** Production config guards are tested (`test_at14_*`); HTTPS reachability **cannot be verified until deployed**. | `test_security.py`, `render.yaml`, `frontend/vercel.json` |

## Invariants

| Invariant | Enforced in |
|---|---|
| I-1 READY needs a fresh approval for the current baseline hash and no open HIGH/CRITICAL exceptions | `domain/readiness.py` (pure), recomputed under the project row lock in `services/state.py` |
| I-2 Baseline change atomically invalidates readiness and stales approvals | `services/changes_service.py::publish_change` (single transaction, project `FOR UPDATE`) |
| I-3 Only an eligible approver who did not author unreleased changes can release | `services/approvals_service.py::decide` |
| I-4 Org-scoped, audited, idempotent where retryable | `services/access.py`, `services/audit.py`, `services/idempotency.py`, outbox + job leases |
| I-5 Raw model output never changes state | Extraction writes candidates only; `extraction/review.py` requires an engineer decision per candidate |

## Changes in this session (after the first full pass)

- **Bugs found by the new UI e2e test and fixed:** extraction review navigated to `/` after publish (relative
  links resolved against the nested review route); the Refresh button in the WI/QC editor sat inside a `<label>`,
  so clicking the label text triggered it.
- **Test bug fixed:** the I-1 invariant helper in `test_reliability.py` read a non-existent
  `Project.valid_approval_id`. It only ran when a race ended READY, so it passed intermittently and hid that the
  READY branch was never actually checked. It now asserts exactly one APPROVED approval matching the current
  baseline hash/version; the affected test then passed 8/8 repeated runs.
- **CI fix (not pushed):** the first GitHub run failed because `minio/minio` is no longer pullable. CI now uses
  RustFS 1.0.4, masks the throwaway keys, and fails fast if storage never becomes healthy.
- **Tests added for documented controls:** JSON log redaction (secrets and bearer tokens) and the AI daily budget
  cap.
- **New validation:** QC checks on a pinned attribute must expect the pinned value (`QC_CHECK_STALE`). The UI now
  shows server validation findings with their codes.
- **Security hardening:** demo seed never prints passwords and requires `FB_DEMO_PASSWORD` in production;
  per-account login rate limit plus `TRUSTED_PROXY_HOPS` (spoofed `X-Forwarded-For` cannot evade limits);
  sanitized `Content-Disposition` on S3 presigned downloads; managed `postgres://` URLs normalized to psycopg.
- **Dependencies:** `npm audit` 10 moderate (dev-only, `minimatch` via ESLint) → 0 via an `overrides` pin;
  `pip-audit` reports none.
- **New tooling:** Playwright AT-12 spec, S3 storage tests (RustFS/MinIO), extraction evaluation harness, benchmark
  script, CI workflow, `render.yaml`, `frontend/vercel.json`, Makefile.

## Known limitations

- Live LLM extraction has never been run; its quality is unmeasured. The evaluation harness supports it (`--provider live`).
- The fixture extractor is a line-grammar parser; prose BOM lines are (correctly) not extracted — recall on prose is 0.
- CI has not passed on GitHub yet. The one run, on `1daf825`, failed before tests on the MinIO image pull. CI
  actions are still on v4/v6 majors; GitHub forces them onto Node 24 with a deprecation warning.
- Not deployed; HTTPS, managed Postgres/Redis, R2 and Render pre-deploy migrations are unverified in the cloud.
- Single-region, single worker (beat embedded); no horizontal worker scaling yet.
- No Sentry/trace sink; observability is JSON logs, correlation IDs and a token-protected `/metrics`.
- Frontend bundle is one 603 kB chunk (189 kB gzip); no code splitting yet.
- `npm audit` is clean only because of the `minimatch` override; revisit when ESLint ships a fixed range.

## Next work

See `PARALLEL_AGENT_HANDOFF.md` for the split across agents. Highest value first:
1. Commit and push the working tree, including the CI fix, and confirm all three CI jobs pass. This needs your
   approval.
2. Deploy per `DEPLOYMENT_GUIDE.md` and run the HTTPS smoke test to close AT-14.
3. Run the evaluation harness and AT-12 with a live model once an API key and budget are approved.
