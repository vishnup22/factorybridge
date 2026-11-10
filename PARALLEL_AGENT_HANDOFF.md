# Parallel agent handoff

Three implementation agents plus one integration owner. Each works in **its own git worktree and branch**, so no
two agents write to the same working directory.

**Verified baseline (start here):** `8a246815d78d689d839a04c2b80d11dadfb94481` on `main`.
CI green: [run 42755647032](https://github.com/vishnup22/factorybridge/actions/runs/42755647032)
(backend ✓, frontend ✓, e2e ✓). Feature branches are fast-forwarded to this commit after CI passed.

| Role | Agent | Worktree (sibling of this repo) | Branch |
|---|---|---|---|
| A: Frontend / product | Cursor | `../factorybridge-ui` | `feat/fb-ui` |
| B: AI / document intelligence | Claude Code | `../factorybridge-ai` | `feat/fb-ai` |
| C: Domain correctness / reliability | GPT-6 Astra | `../factorybridge-core` | `feat/fb-core` |
| Integration owner: CI, deploy, contracts, merges | the integration agent, under your supervision | `../factorybridge-platform` | `feat/fb-platform` |

`main` (this directory) is the integration target. Nobody edits it directly. Changes reach it only by the
integration owner merging reviewed branches.

## Before anyone starts

1. Confirm your worktree branch tip equals `8a24681` (or a later merge of `main`).
2. Create your isolated test DB / Redis index (table below) before running integration tests.
3. Do not deploy or request paid infrastructure until the integration owner says so.

## Shared rules

- Read `AGENTS.md`, `FACTORYBRIDGE_MASTER_SPEC.md`, `ARCHITECTURE.md` and `API_CONTRACTS.md` first.
- **No commits to `main`, no pushes, no deploys** without your approval. Agents commit to their own branch only.
- Synthetic data only. Never connect to real equipment. Never weaken auth or approval checks to make a test pass.
- **AI boundary (spec I-5):** model output creates *candidates* only. Authoritative records change only through
  engineer review (`services/extraction/review.py`) and the existing services. No agent may add a path where model
  output, or an LLM, approves anything or sets readiness.
- Don't claim results you didn't run. Each branch's PR description lists the commands executed and their output
  summary.

### Isolating local test resources

The integration suite runs `TRUNCATE` on every table of its test database between tests. If two worktrees share
`factorybridge_test`, they corrupt each other. Give each worktree its own database and Redis index (the fixtures
apply migrations automatically):

| Worktree | `TEST_DATABASE_URL` database | `REDIS_URL` |
|---|---|---|
| ui | `factorybridge_test_ui` | `redis://127.0.0.1:6379/11` |
| ai | `factorybridge_test_ai` | `redis://127.0.0.1:6379/12` |
| core | `factorybridge_test_core` | `redis://127.0.0.1:6379/13` |
| platform | `factorybridge_test` (default) | `redis://127.0.0.1:6379/15` (default) |

```powershell
docker compose exec -T postgres createdb -U factorybridge factorybridge_test_ui   # once per worktree
$env:TEST_DATABASE_URL = "postgresql+psycopg://factorybridge:factorybridge@127.0.0.1:55432/factorybridge_test_ui"
$env:REDIS_URL = "redis://127.0.0.1:6379/11"
```

Only **one** compose stack can run, because ports 8000 and 5173 are fixed. It runs from this directory, owned by
the integration owner. Other agents may use it for manual checks but must not `down -v` it.

## File ownership

An owner may change its files freely on its branch. **Shared contract files** change only through a coordinated
PR that the integration owner merges first.

| Owner | Paths |
|---|---|
| **A (UI)** | `frontend/src/**` (except the generated API files), `frontend/e2e/**`, `frontend/index.html`, `frontend/eslint.config.*`, `frontend/vite.config.ts` |
| **B (AI)** | `backend/app/services/extraction/**`, `backend/app/integrations/**`, `backend/app/services/documents_service.py`, `backend/tests/unit/test_extraction_unit.py`, `scripts/evaluate_extraction.py`, `scripts/generate_demo_pdfs.py`, `demo/fixtures/*.pdf`, `docs/evaluation*` |
| **C (core)** | `backend/app/domain/**`, `backend/app/services/{access,approvals_service,artifacts_service,audit,bom_service,changes_service,exceptions_service,idempotency,outbox,plan_service,state}.py`, `backend/app/workers/**`, `backend/app/models/**`, `backend/alembic/**`, `demo/seed.py`, `demo/fixtures/*.{json,csv}`, `backend/tests/unit/test_{bom,graph_impact,artifacts_readiness}.py`, and `backend/tests/integration/test_{acceptance,flagship,reliability}.py` |
| **Integration** | `.github/**`, `docker-compose.yml`, `*/Dockerfile`, `render.yaml`, `frontend/vercel.json`, `frontend/package.json`, `backend/pyproject.toml` + `uv.lock`, `backend/app/{main.py,core/**,observability/**}`, `backend/app/api/deps.py`, `backend/app/services/storage.py`, `backend/tests/integration/test_{security,s3_storage}.py`, `scripts/{demo_flow,benchmark}.py`, `Makefile`, the root `*.md` files |

**Shared contracts:**

- `backend/app/schemas/api.py` and the route files in `backend/app/api/v1/`. These define the HTTP contract.
- `frontend/src/api/openapi.json` and `schema.d.ts`. These are generated by `make openapi` and never hand-edited.
- `demo/fixtures/flagship_scenario.json` and `expected_impact.json`. The flagship scenario is the acceptance oracle
  shared by backend tests, `demo_flow.py` and Playwright.
- **Migrations:** only C creates Alembic revisions, so the migration chain keeps one head. Others ask C.

## Contracts between agents

- **C ↔ A, HTTP:** OpenAPI. A consumes the generated types only. If A needs a field, A asks for it, and the
  change lands as: schema in `api.py` + route + `make openapi` + backend test, merged before A's UI uses it.
- **B → C, extraction output:** `services/extraction/schema.py` (candidate model with evidence) and
  `review.py::accept`. B may improve how candidates are produced. B may not change how they become BOM revisions
  or changes; that path belongs to C.
- **C, domain purity:** `app/domain/*` stays free of DB, I/O and FastAPI. Readiness stays a pure function of facts
  (`domain/readiness.py`).

## Merge protocol (integration owner)

1. The branch is rebased on the latest `main`, and the PR lists the commands run.
2. The integration owner runs the full gate in the platform worktree:
   `uv run ruff check . ../scripts ../demo`, `uv run ruff format --check …`, `uv run mypy`, `uv run alembic check`,
   `uv run pytest -q`, `npm run lint && npm run typecheck && npx vitest run && npm run build`, then on the live
   stack `scripts/demo_flow.py` and `npx playwright test`.
3. Merge order when several branches are ready: **C → B → A** (contracts first, UI last). Re-run the gate after
   each merge.
4. Pushing `main` and deploying still need your approval each time.

## Starting backlog per agent

From `IMPLEMENTATION_STATUS.md` known limitations, highest value first:

- **A (UI):**
  - Route-level code splitting. The bundle is currently one 603 kB chunk.
  - An accessibility pass: keyboard, focus and labels.
  - A Playwright spec for the CSV-intake path and the customer-only view.
  - Clearer display of FIXTURE vs LIVE AI mode.
- **B (AI):**
  - Run `evaluate_extraction.py --provider live` once you approve a key and budget, and record the real numbers.
  - LLM extraction of prose BOM lines, with evidence re-verification. Current prose recall is 0.
  - A larger and nastier evaluation set: tables, multi-page, near-duplicate parts.
- **C (core):**
  - Run beat as a separate singleton so workers can scale horizontally.
  - Property-based tests for readiness and rules. Hashing and traversal already have Hypothesis tests.
  - Waiver expiry and policy.
  - Audit export.
- **Integration:**
  - Get CI green on GitHub.
  - Bump the CI actions to the current majors and verify on a run.
  - Deploy per `DEPLOYMENT_GUIDE.md` and run the HTTPS smoke test to close AT-14.
  - Add error tracking.
