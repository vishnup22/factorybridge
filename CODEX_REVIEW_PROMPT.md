# Codex — Independent Verification Prompt

Read `FACTORYBRIDGE_MASTER_SPEC.md` and `AGENTS.md`. Treat the implementation as untrusted until you run tests. Review the current branch for security and correctness, focusing on:

1. BOM R1/R2 immutability and canonical content/baseline hashes.
2. Deterministic DAG impact computation, stable ordering, cycle detection, and no impact on unrelated nodes.
3. Approval freshness, separation of duties, and atomic R2 publication versus approval race (use concurrent integration tests).
4. Idempotency-Key behavior; transactional outbox/event replay; worker crashes; duplicate issues.
5. Cross-organization IDOR, customer approval denial, upload MIME/size constraints, prompt injection barriers, secret handling.
6. AI schema/provenance verification and correct handling of `UNKNOWN` compatibility.
7. Honest README: actual test results, deployed features, benchmarks, and non-affiliation.

For every bug, give: severity, reproduction command, failing test, smallest credible fix. Add tests first where practical. Do not refactor unrelated working code. Run lint/types/unit/integration/security tests and report **exact** results. Do not claim unexecuted tests passed.
