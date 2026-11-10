# Cursor — Kickoff Prompt for FactoryBridge

Paste the text under **Prompt** into Cursor Agent mode. Attach or place `FACTORYBRIDGE_MASTER_SPEC.md` in the workspace first. The master spec is the source of truth.

---

## Prompt

You are acting as a founding full-stack engineer building **FactoryBridge**, an independent, deployable demo of AI-assisted manufacturing change management. It is tailored to the publicly described problems of robotics-native contract manufacturing but is not affiliated with any company.

**MANDATORY:** Read `FACTORYBRIDGE_MASTER_SPEC.md` end-to-end, especially invariants I-1 to I-5, phases 0–5, API contract, critical acceptance tests AT-01 to AT-14, security, and deployment. Read `AGENTS.md`. Implement the requirements in **small verified increments**. Do not produce only a plan or UI mock. Create executable code, DB migrations, tests, fixtures, Docker setup, and documentation.

### First response before coding

1. Summarize the architecture (modular monolith API + worker + Postgres + Redis + private storage + React).
2. Identify assumptions and any contradictions in the spec; prefer simple defaults over stopping.
3. Translate Phase 0 and Phase 1 into explicit tasks, test cases, and files.
4. Then begin implementation. No extra confirmation is needed unless a real security or dependency blocker exists.

### Phase gates

**Phase 0 (do this first):** Create repo structure, FastAPI app with `/api/v1/health/live`, React/Vite frontend, Postgres + Redis Docker Compose, `.env.example`, Alembic, Makefile, pre-commit lint/test/CI, and seed infrastructure. Run the app and report actual commands/results.

**Phase 1:** Build the deterministic **Warehouse Scout V1** demo *without* LLMs. Implement revisioned BOM R1/R2, stable line keys, content hashing, explicit compatibility rules, dependency DAG, impact traversal, exceptions, and atomic invalidation of prior readiness. Build a CLI/API demonstration of ACT-100 (24V/M4-40) → ACT-200 (48V/M6-50) with accurate impact on supplier, compatibility, work instruction, QC and approval. Unit/integration tests must pass before proceeding.

**Phase 2:** Add role-based authentication and approval gate, stale baseline rejection, separation of duties, transactional outbox, idempotency and concurrency tests. R2 must never inherit R1 approval. No silent bypass.

**Phase 3:** Build usable engineering screens: BOM diff, engineering change editor, impact graph, exception inbox, baseline-specific approval, and audit timeline. Every screen must use working backend data, not hardcoded results.

**Phase 4:** Add private file upload, CSV parser, PDF text extraction, optional Anthropic structured candidate extraction, page/source evidence verification, engineer review, and fixture mode. The model may suggest but must never approve or set READY.

**Phase 5:** Add production deployment documentation, CI/CD, smoke/security tests, monitoring, controlled demo credentials, and real demo URL. Never assert deployed unless deployed and verified.

### Acceptance priorities

AT-01–AT-14 in the spec are the definition of success. In particular, stale approvals, engineering change impact, append-only auditing, authorization, and replay behavior are mandatory.

### Engineering instructions

- Prefer SQLAlchemy 2 and Alembic; never rely on in-memory business state.
- Keep domain code testable independently of HTTP and Claude.
- Never invent benchmarks, results, tests, or working features.
- Do not expose raw DB/Redis ports publicly or commit credentials.
- Create a short `IMPLEMENTATION_STATUS.md` at repo root with completed modules, commands run, tests/results, blockers, and next milestones. Update this after every phase.
- After each phase, summarize: files changed, tests executed, failures fixed, known limitations, next phase.
- If one-week timing forces tradeoffs, prioritize a fully correct R1→R2→BLOCKED demo over additional features. Avoid scaffolding unimplemented modules merely to check boxes.

**Start Phase 0 now and continue into Phase 1 as far as you can with real validated code.**
