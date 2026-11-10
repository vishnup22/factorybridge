# Demo script (~90 seconds) and fixture grammar

All data is synthetic. "Warehouse Scout V1" and every part, supplier and document are fictional.

## Accounts

`python -m demo.seed` creates `customer@`, `engineer@`, `engineer2@`, `approver@`, `approver2@` and `admin@demo.example`.
Passwords are random and written to the gitignored `backend/var/demo_credentials.txt`, or come from
`FB_DEMO_PASSWORD`. They are never printed.

## Walkthrough (matches `frontend/e2e/flagship.spec.ts`)

1. **Customer:** create a project (SKU `WS-100`, quantity 100) and upload `demo/fixtures/requirements_v1.pdf`.
   The header badge reads "AI: fixture (no model calls)".
2. **Engineer:** open the extraction review. Every candidate shows page and snippet evidence. Choose *Accept all
   verified*, then *Publish reviewed values as R1*. Apply the plan template, which adds the dependency graph,
   compatibility rules, supplier selections, work instructions and QC plan, then request approval.
3. **Approver:** approve. The banner shows **Released**.
4. **Engineer, EC-001:** in the change editor, set `ACTUATOR_MAIN` to `ACT-200` rev B, 48 V, `M6-50`. The impact
   preview lists:

   | Node | Severity | Reason |
   |---|---|---|
   | `COMPATIBILITY_CHECK:ACTUATOR_MOUNT` | CRITICAL | `MOUNTING_INTERFACE_CHANGED` (FAIL) |
   | `COMPATIBILITY_CHECK:MOTOR_VOLTAGE` | CRITICAL | `VOLTAGE_COMPATIBILITY_CHANGED` (FAIL) |
   | `READINESS_APPROVAL:PROJECT` | CRITICAL | `READINESS_APPROVAL_STALE` |
   | `QC_PLAN:MOTOR_FUNCTION_TEST` | HIGH | `QC_PLAN_STALE` |
   | `SUPPLIER_SELECTION:ACTUATOR_MAIN` | HIGH | `SOURCING_REVALIDATION_REQUIRED` |
   | `WORK_INSTRUCTION:INSTALL_ACTUATOR` | HIGH | `WORK_INSTRUCTION_STALE` |

   `WORK_INSTRUCTION:PACKAGE_BATTERY`, `COMPATIBILITY_CHECK:POWER_SUPPLY` and the battery and sensor lines are
   **not** impacted. Publish to get **R2**. The project shows **Not releasable**, and the R1 approval is marked
   stale.
5. **Engineer, EC-002:** move the controller, battery and chassis to the 48 V / `M6-50` platform to get **R3**.
6. **Resolve the exceptions** with new artifact revisions: supplier picks, a rewritten work instruction, and a QC
   plan. Submitting the QC plan with `expected=24` is rejected with `QC_CHECK_STALE`, because the pinned
   instruction is 48 V. Fix it to 48 and resubmit.
7. **Approver:** approve R3. The customer sees **READY**, and the audit page shows the whole sequence.

The same flow without a browser: `scripts/demo_flow.py` (see `DEPLOYMENT_GUIDE.md`).

## Fixture PDF line grammar

The FIXTURE provider (`backend/app/services/extraction/providers.py`) only recognizes these lines. Anything else,
including prose, is ignored rather than guessed. `scripts/generate_demo_pdfs.py` writes PDFs in this grammar.

```text
Product: <name>
Product SKU: <sku>
Target quantity: <integer>
REQ <key>: <value> [<unit>]                       e.g.  REQ drive_supply_voltage: 24 V
BOM LINE <LINE_KEY>: <part> rev <rev>, qty <n> <unit>[, <attr>=<value>]* [| <description>]
                                                   e.g.  BOM LINE BATTERY: BAT-24 rev A, qty 1 each, voltage_v=24 | Synthetic battery pack
```

`<LINE_KEY>` is uppercase `[A-Z0-9_]`, attribute names are lowercase `[a-z_]`, and attribute values can't contain
`,` or `|`. Regenerate the fixtures with `cd backend; uv run python ../scripts/generate_demo_pdfs.py`.
