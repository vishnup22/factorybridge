# Threat model

Scope: the FactoryBridge API, worker, SPA and their stores. All data is synthetic, and the system controls no
equipment. Even so, it is built as if a wrong READY status had real cost. The asset that matters most is the
**integrity of readiness**: nobody, and no model, should be able to make an unvalidated design look releasable.

## Assets

1. Readiness status, approvals and their baseline binding.
2. BOM revisions, artifacts and the audit trail (append-only).
3. Uploaded documents (private).
4. Credentials and secrets: JWT secret, DB/Redis/object-storage keys, AI key, demo passwords.
5. AI budget.

## Trust boundaries

Browser to API (internet). API and worker to Postgres, Redis and object storage (private network). Worker to the
model provider (internet, optional). Uploaded files are untrusted input, and so is everything inside them.

## Threats and mitigations

| Threat | Mitigation | Verified by |
|---|---|---|
| Stale or replayed approval makes R2 READY | Approval bound to baseline hash and version; atomic invalidation under the project row lock; pure readiness function | AT-07, AT-08 race tests, invariant check |
| Engineer approves their own change | Separation of duties on request and on decision; role re-read from the DB on every request | AT-05, AT-06 |
| Cross-tenant access | Every query scoped by organization; other orgs' resources return 404 | `test_security.py` |
| Prompt injection in a document ("approve this project") | Model output only creates candidates. Each candidate's snippet must be found on the cited page or it is marked unverified. Engineer review is required. No tool or permission exists for the model to change state. | AT-11, injection fixtures, evaluation (0 injected candidates) |
| Fabricated extraction from malformed, scanned or encrypted PDFs | Parser fails closed: `PDF_MALFORMED`, `PDF_NO_TEXT`, `PDF_ENCRYPTED`, `PDF_TOO_MANY_PAGES` | AT-11 |
| Malicious upload (active content, disguised type, path traversal) | Magic-byte type check, extension mismatch rejection, `/JavaScript` `/Launch` `/EmbeddedFile` rejected, 10 MB cap, server-generated storage keys only, sanitized `Content-Disposition` | `test_acceptance.py`, `test_s3_storage.py` |
| Public document exposure | Private bucket; access only through short-lived presigned URLs after an authorization check | S3 test: unsigned GET returns 403 |
| Credential stuffing or brute force | Argon2 hashes; Redis rate limit per IP and per account; client IP taken only from trusted proxy hops | `test_security.py` (spoofed `X-Forwarded-For`) |
| Token theft | 30-minute HS256 JWT with required `exp` and `sub`; kept in `sessionStorage`, not cookies (no CSRF surface) or `localStorage`; strict CSP; bearer tokens redacted from logs | CSP in `vercel.json`, `test_json_logs_redact_secrets_and_bearer_tokens` |
| Insecure production config | Startup refuses dev JWT secret, wildcard or http CORS, local DB credentials, non-S3 storage, or AI enabled without a key; seed requires `FB_DEMO_PASSWORD` | AT-14 config tests |
| Duplicate side effects from retries or worker death | Idempotency keys with stored replay; checksum dedupe; job leases; outbox with `SKIP LOCKED`; idempotent consumers | AT-09, AT-10 |
| Audit tampering | Append-only tables enforced by DB triggers; ordered `seq` | `test_immutability_enforced_in_database` |
| Runaway AI spend | `AI_DAILY_BUDGET_USD` cap on the trailing 24 h estimated cost per org, checked before every live call and non-retryable; fixture mode by default | `test_live_extraction_refused_once_daily_budget_is_spent` (threshold only; spend accounting against real runs is untested) |
| Exposed DB or Redis ports | Local: bound to `127.0.0.1`. Render: `ipAllowList: []` (private network only) | `docker-compose.yml`, `render.yaml` |

## Accepted risks and gaps

- `/api/openapi.json` stays public in production. Interactive docs are disabled. The schema reveals endpoint shapes
  but no data, and every endpoint still requires auth.
- XSS would expose the token in `sessionStorage`. The mitigations are React escaping, a strict CSP with no inline
  scripts, and a short token lifetime. HttpOnly cookie sessions would be stronger, at the cost of CSRF handling.
- There is no token revocation list. Deactivating a user takes effect immediately because each request re-reads
  the user, but a stolen token for an active user stays valid until it expires.
- There is no malware scanning of uploads beyond the structural PDF checks above.
- None of this has been tested in a deployed environment, and there has been no external penetration test.
