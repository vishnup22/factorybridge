# Test results

Only results of commands that were actually executed are recorded here. Environment: Windows 11, Python 3.12.10
(AMD64, 16 CPUs), Node via npm, Docker Desktop running the compose stack (Postgres 16, Redis 7). Date: 2026-11-09.

## Final verification run (final code, rebuilt images)

| Check | Command | Result |
|---|---|---|
| Backend tests | `cd backend; uv run pytest -q` with `S3_TEST_*` pointing at RustFS 1.0.4 | **133 passed** (122.6 s), including S3 and the two new log-redaction and AI-budget tests. Without `S3_TEST_*`, the 2 S3 tests skip (earlier final-code run: 129 passed, 2 skipped, before the new tests). |
| Lint / format | `uv run ruff check . ../scripts ../demo`; `uv run ruff format --check …` | Clean; 79 files formatted |
| Types | `uv run mypy` | No issues in 57 source files |
| Migrations | `uv run alembic check` | No new upgrade operations detected |
| Frontend lint / types | `npm run lint`; `npm run typecheck` | Clean |
| Frontend unit tests | `npx vitest run` | **13 passed** (3 files) |
| Frontend build | `npm run build` | JS 603.26 kB (188.62 kB gzip), CSS 37.36 kB |
| Dependency audit | `npm audit --audit-level=moderate`; `uv run --with pip-audit pip-audit` | 0 vulnerabilities; no known vulnerabilities |
| Live HTTP demo | `python scripts/demo_flow.py --base-url http://127.0.0.1:8000 --credentials backend/var/demo_credentials.txt` | **DEMO PASSED** — R1 → EC-001 BLOCKED → resolve → R3 READY with fresh approval; 72 audit events |
| AT-12 UI e2e | `cd frontend; npx playwright test` (Chromium, against the compose stack) | **1 passed** (14.1 s). Earlier runs: 30.8 s, 32.8 s |

## Earlier targeted runs this session

- **S3 storage:** `S3_TEST_* uv run pytest tests/integration/test_s3_storage.py` → 2 passed against a cached local
  MinIO image, and 2 passed again against **RustFS 1.0.4** (the new CI image). Covers round-trip, unsigned access
  denied, presigned download with a safe `Content-Disposition`, and invalid keys rejected.
- **CI workflow:** `actionlint` → clean (also after the RustFS change).

## GitHub Actions (run on GitHub, not locally)

| Run | Commit | Result |
|---|---|---|
| [42751873276](https://github.com/vishnup22/factorybridge/actions/runs/42751873276) | `1daf825` (main) | **Failed.** Frontend passed. Backend failed pulling `minio/minio` (community images no longer published). Tests and e2e did not run. |
| [42755647032](https://github.com/vishnup22/factorybridge/actions/runs/42755647032) | `8a24681` (main) | **Success.** frontend 17s ✓ · backend 1m48s ✓ · e2e 6m27s ✓. S3 tests used pinned `rustfs/rustfs:1.0.4`. |

## Failure found during the final run

The first final-run attempt gave `1 failed, 128 passed`: `test_concurrent_decisions_on_one_approval` failed with
`AttributeError: 'Project' object has no attribute 'valid_approval_id'` in the I-1 invariant helper. This was a
test bug, not a product bug — the helper's READY branch referenced a field that doesn't exist, so it crashed
whenever the race ended in READY (4 of 5 isolated reruns) and passed otherwise. Fixed to check the project's
single APPROVED approval against the current baseline hash/version; then 8/8 isolated reruns passed, the reliability
file passed (20), and the full suite is the result above.

## Acceptance tests

AT-01 to AT-13 pass via the backend suite (named `test_at0X_*` / `test_flagship.py` / `test_reliability.py`) and, for
AT-12, Playwright. AT-14 is partially verified: production config guards are tested, HTTPS is not (not deployed).
Mapping: [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md#acceptance-tests-spec-12).

## Performance (`scripts/benchmark.py`, local, one machine — indicative only)

| Measurement | Runs | p50 | p95 |
|---|---|---|---|
| `assess_impact`, synthetic DAG 2000 nodes / 5000 edges, 5 changed lines (162 impacted) | 30 | 3.88 ms | 5.16 ms |
| `find_cycle`, same graph | 30 | 3.68 ms | 4.30 ms |
| API `GET /projects/{id}` (local compose) | 50 | 44.25 ms | 68.01 ms |
| API `GET …/changes/{cid}/impact` | 50 | 39.28 ms | 58.92 ms |
| API `GET …/dependency-graph` | 50 | 35.47 ms | 67.86 ms |

## Extraction evaluation (`scripts/evaluate_extraction.py`, FIXTURE provider, seed 20261109, 40 scenarios)

Full numbers in `docs/evaluation_results.json`; discussion in [`docs/evaluation.md`](docs/evaluation.md).
Grammar-conforming lines (203): recall/part/revision/quantity/attributes 1.0. Prose lines: recall 0.0 (0/12, by
design — not fabricated). Evidence validity 1.0 (215/215); unsupported assertions 0; injected candidates 0;
missing-field detection 6/6; contradictions caught 6/6. Perfect scores on in-grammar lines are expected for a
deterministic parser and say nothing about live-LLM quality.

## Not run

- Live LLM extraction (no `ANTHROPIC_API_KEY`) — AT-12 live variant and `--provider live` evaluation.
- Any cloud deployment, HTTPS smoke test, managed Postgres/Redis, Cloudflare R2.
- Load testing beyond the single-client benchmark above. No coverage percentage was measured.
