# API contracts (`/api/v1`)

The machine-readable source of truth is the OpenAPI schema served at `/api/openapi.json` (interactive docs at
`/api/docs` outside production). The frontend's types are generated from it into `frontend/src/api/schema.d.ts`;
`npm run typecheck` fails if the two drift. This file documents the cross-cutting conventions and the endpoint map.

## Conventions

- **Auth:** `Authorization: Bearer <JWT>` from `POST /auth/login`. Role and active status are re-read from the DB on
  every request; a token never carries authority on its own.
- **Success envelope:** `{"data": <payload>, "meta": {...}}` (lists put pagination in `meta`). `GET /auth/me` returns
  its object unwrapped.
- **Errors:** RFC 7807 `application/problem+json`:
  `{"type","title","status","detail","code","correlation_id","extra"}`. `code` is stable and machine-readable;
  `extra` carries details such as `issues` (change-set validation), `blockers`, or `validation.findings`.
- **Correlation:** send `X-Correlation-ID` (8–64 `[A-Za-z0-9-]`) or one is generated; it is echoed on every response and in logs.
- **Optimistic concurrency:** state-changing project calls take `expected_baseline_version` and/or
  `expected_baseline_hash`; a mismatch returns 409 (`STALE_BASELINE_VERSION`, `STALE_BASE_REVISION`, `REVIEWED_BASELINE_MISMATCH`, `APPROVAL_STALE`).
- **Idempotency:** `Idempotency-Key` (1–200 chars) is honored on extraction accept, change publish and approval
  decision. Same key + same body → stored response with `Idempotent-Replayed: true`; same key + different body →
  409 `IDEMPOTENCY_KEY_REUSED`. Uploads are deduplicated by content checksum (200 with the existing document).
- **Org isolation:** resources in another organization return 404, not 403.

## Endpoints

| Method | Path | Roles | Notes |
|---|---|---|---|
| POST | `/auth/login` | public | Rate-limited per IP and per account; generic failure message |
| GET | `/auth/me` | any | User, organization, `ai_mode` (FIXTURE/LIVE), environment |
| GET, POST | `/projects` | read: all · create: customer, engineer, admin | |
| GET | `/projects/{id}` | all (customer view redacted) | Includes `status`, `blockers`, `kpis`, baseline hash/version |
| GET | `/projects/{id}/readiness` | all | Derived readiness + blockers |
| GET, POST | `/projects/{id}/documents` | upload: customer, engineer | multipart `file`; PDF or CSV, ≤ 10 MB; returns document + job |
| GET | `/documents/{id}` · `/documents/{id}/download` | project members | Download: presigned URL (S3) or streamed (local) |
| GET | `/documents/{id}/extraction` | engineer, admin | Run metadata + candidates with evidence status |
| POST | `/documents/{id}/extraction/accept` | engineer | One decision per candidate (ACCEPT / EDIT+note / REJECT); creates R1 or a PROPOSED change |
| GET | `/jobs/{id}` | project members | Processing job status/attempts |
| GET | `/projects/{id}/bom/revisions` · `/bom/revisions/{rev}` · `/bom/diff?from=&to=` | all | Immutable revisions with hash |
| GET, POST | `/projects/{id}/changes` | propose: engineer | Change set of ADD/UPDATE/REMOVE ops against `base_revision_number` |
| GET | `/projects/{id}/changes/{cid}` · `/impact` | all (impact detail: staff) | Live preview while PROPOSED; persisted after publish |
| POST | `/projects/{id}/changes/{cid}/publish` | engineer | Atomic: new revision, invalidations, exceptions, stale approvals |
| POST | `/projects/{id}/changes/{cid}/withdraw` | engineer | |
| GET | `/projects/{id}/dependency-graph` | staff | Nodes `TYPE:ID`, edges `source → dependent` |
| POST | `/projects/{id}/dependency-edges` | engineer | Cycle and node-key validation |
| POST | `/projects/{id}/plan/apply-template` | engineer | Synthetic Warehouse Scout plan (`warehouse_scout_v1`) |
| GET | `/projects/{id}/compatibility-rules` | staff | |
| GET, POST | `/projects/{id}/artifacts` | create: engineer | New artifact revision, validated server-side |
| GET | `/projects/{id}/exceptions?status=` | all | |
| POST | `/projects/{id}/exceptions/{eid}/resolve` | engineer | Requires a new VALID artifact revision + note |
| POST | `/projects/{id}/exceptions/{eid}/waive` | approver | Non-critical only; rationale ≥ 10 chars |
| GET | `/projects/{id}/approvals` | all | |
| POST | `/projects/{id}/approvals/request` | engineer | Requires no non-approval blockers; auto-assigns an eligible approver |
| POST | `/projects/{id}/approvals/{aid}/decision` | assigned approver | `decision`, `reason`, `expected_baseline_hash` |
| GET | `/projects/{id}/audit` · `/admin/audit` | staff · admin | Ordered by `seq`; append-only |
| GET | `/catalog/suppliers?part_number=` | staff | Synthetic catalog |
| GET, POST, PATCH | `/admin/users` · `/admin/users/{uid}` | admin | Cannot change own role or deactivate self |
| GET | `/health/live` | public | Process liveness |
| GET | `/health/ready` · `/metrics` | `X-Health-Token` when `HEALTH_TOKEN` is set | DB, Redis, storage, migration revision, outbox backlog; Prometheus text |

"Staff" = engineer, approver, admin.

## Frequently used error codes

`INVALID_CREDENTIALS`, `RATE_LIMITED`, `ROLE_FORBIDDEN`, `APPROVER_INELIGIBLE`, `APPROVAL_STALE`,
`REVIEWED_BASELINE_MISMATCH`, `STALE_BASELINE_VERSION`, `STALE_BASE_REVISION`, `IDEMPOTENCY_KEY_REUSED`,
`INVALID_CHANGE_SET`, `INVALID_REVIEW_DECISIONS`, `NOT_WAIVABLE`, `RESOLUTION_EVIDENCE_INVALID`,
`RESOLUTION_EVIDENCE_STALE`, `UNSUPPORTED_FILE_TYPE`, `EXTENSION_MISMATCH`, `SUSPICIOUS_CONTENT`, `PDF_MALFORMED`,
`PDF_NO_TEXT`, `FILE_TOO_LARGE`, `REQUEST_INVALID`.

## Contract ownership for parallel work

Changing a request/response shape requires: update the Pydantic schema in `backend/app/schemas/api.py`,
regenerate `frontend/src/api/openapi.json` + `schema.d.ts` (`make openapi`), and get backend and frontend tests
green in the same change. See `PARALLEL_AGENT_HANDOFF.md`.
