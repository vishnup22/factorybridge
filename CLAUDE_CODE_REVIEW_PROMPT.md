# Claude Code — Architecture and Production Reliability Review Prompt

Read `FACTORYBRIDGE_MASTER_SPEC.md`, `AGENTS.md`, and the actual implementation. Perform a senior/founding-engineer design review, not generic suggestions. Evaluate:

- Is FactoryBridge solving its one compelling use case (engineering-change blast-radius + approval freshness) end to end?
- Do module boundaries prevent the AI adapter from updating approved BOMs or releasing manufacturing readiness?
- Are DB transactions, outbox behavior, idempotency, and replay failure-safe?
- Does the revision-bound baseline hash cover all dependencies and rule versions, and are stale approvals rejected atomically?
- Can the demo survive missing API keys and mocked LLM responses without pretending synthetic fixtures are model outputs?
- Are errors, unresolved issues, unsupported PDF types, ambiguous BOM rows, concurrent mutations, and timeouts handled?
- Is the auth/storage/deployment setup safe for a public portfolio URL?
- Could a skeptical founder inspect the 90-second demo, GitHub tests, ADRs, and measurements and understand technical value?

Return a prioritized list: `P0 blocking`, `P1 worthwhile`, `P2 defer`. Cite exact file paths/lines and runnable reproduction steps. Suggest minimal changes and avoid architecture astronautics. Do not invent tests, measurements, or deployed features.
