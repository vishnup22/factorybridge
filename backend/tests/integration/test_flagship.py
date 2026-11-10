"""Flagship scenario (spec section 0 / STEP 5) through the HTTP API with the real worker pipeline.
Covers AT-01, AT-02, AT-03, AT-07, AT-13."""

from sqlalchemy import text

from app.core.db import session_factory
from tests.fixtures import EC001, EC002

WI_INSTALL_R3 = {
    "document_code": "WI-ACT-01",
    "steps": [
        "Verify ACT-200 rev B against traveler",
        "Fasten actuator using M6-50 pattern on CHS-02",
        "Connect harness to CTRL-48",
        "Torque fasteners per synthetic spec sheet rev B",
    ],
    "references": {
        "ACTUATOR_MAIN": {
            "part_number": "ACT-200",
            "part_revision": "B",
            "attributes": {"mount_pattern": "M6-50", "voltage_v": 48},
        },
        "CHASSIS": {"part_number": "CHS-02", "part_revision": "A"},
    },
}
WI_BATTERY_R3 = {
    "document_code": "WI-BAT-01",
    "steps": ["Inspect 48V label", "Insert into foam carrier", "Seal and label"],
    "references": {"BATTERY": {"part_number": "BAT-48", "part_revision": "A", "attributes": {"voltage_v": 48}}},
}


def qc_r3(wi_revision: int) -> dict:
    return {
        "document_code": "QC-ACT-01",
        "checks": [
            {"name": "Supply voltage at actuator", "parameter": "voltage_v", "expected": 48},
            {"name": "No-load spin test", "parameter": "spin", "expected": "PASS"},
        ],
        "references": {
            "ACTUATOR_MAIN": {"part_number": "ACT-200", "part_revision": "B", "attributes": {"voltage_v": 48}},
            "CONTROLLER": {"part_number": "CTRL-48", "part_revision": "A"},
        },
        "artifact_refs": {"WORK_INSTRUCTION:INSTALL_ACTUATOR": wi_revision},
    }


def test_flagship_r1_ready_ec001_blocked_resolved_reapproved(api):
    # 1-2. customer creates project and uploads the synthetic BOM CSV
    pid = api.create_project("customer")
    up = api.upload(pid, "bom_v1.csv", who="customer")
    assert up.status_code == 202
    doc = up.json()["data"]
    assert doc["job"]["status"] == "QUEUED"
    assert api.project(pid)["status"] == "EXTRACTING"
    assert api.run_worker() == 1
    assert api.get("customer", f"/documents/{doc['id']}").json()["data"]["processing_status"] == "REVIEW_REQUIRED"

    # 3-4. engineer reviews evidence and accepts -> immutable R1
    ex = api.get("engineer", f"/documents/{doc['id']}/extraction").json()["data"]
    assert ex["run"]["provider_mode"] == "CSV_DETERMINISTIC"
    assert {c["evidence_status"] for c in ex["candidates"]} == {"VERIFIED"}
    assert all(c["source_excerpt"] for c in ex["candidates"])
    acc = api.accept_all(doc["id"])
    assert acc.status_code == 200 and acc.json()["data"]["bom_revision"] == 1
    r1 = api.get("engineer", f"/projects/{pid}/bom/revisions/1").json()["data"]
    assert len(r1["items"]) == 5
    customer_view = api.get("customer", f"/projects/{pid}/bom/revisions/1").json()["data"]
    assert all(i["provenance_json"] == {} for i in customer_view["items"])

    # 5. manufacturing plan: supplier selection, compat checks, WI, QC
    t = api.post("engineer", f"/projects/{pid}/plan/apply-template", {"template": "warehouse_scout_v1"})
    assert t.status_code == 200
    assert {a["status"] for a in t.json()["data"]["artifacts_created"]} == {"VALID"}
    p = api.project(pid)
    assert p["status"] == "REVIEW_REQUIRED" and [b["code"] for b in p["blockers"]] == ["APPROVAL_REQUIRED"]

    # 6. approver approves R1 -> READY
    req = api.request_approval(pid)
    assert req.status_code == 201
    a1 = req.json()["data"]
    assert a1["bom_revision_number"] == 1 and a1["assigned_approver_email"].startswith("approver")
    assert api.approve(pid, a1["id"], expected_baseline_hash=a1["baseline_hash"]).status_code == 200
    assert api.project(pid)["status"] == "READY"

    # 7. EC-001 proposed, previewed (not persisted), published -> R2
    ch = api.propose(pid, EC001)
    assert ch.status_code == 201
    cid = ch.json()["data"]["id"]
    assert ch.json()["data"]["code"] == "EC-001"
    preview = api.get("engineer", f"/projects/{pid}/changes/{cid}/impact").json()["data"]
    assert preview["persisted"] is False and api.project(pid)["status"] == "READY"
    pub = api.publish(pid, cid, expected_baseline_version=api.project(pid)["baseline_version"])
    assert pub.status_code == 200, pub.text
    impact = pub.json()["data"]

    # 8. deterministic impact on >= 4 artifacts incl. supplier, compat, WI, QC, approval
    nodes = {i["node"]: i for i in impact["impacted"]}
    assert impact["to_revision"] == "R2" and impact["readiness"] == "BLOCKED"
    assert {
        "SUPPLIER_SELECTION:ACTUATOR_MAIN",
        "COMPATIBILITY_CHECK:MOTOR_VOLTAGE",
        "COMPATIBILITY_CHECK:ACTUATOR_MOUNT",
        "WORK_INSTRUCTION:INSTALL_ACTUATOR",
        "QC_PLAN:MOTOR_FUNCTION_TEST",
        "READINESS_APPROVAL:PROJECT",
    } == set(nodes)
    assert nodes["COMPATIBILITY_CHECK:MOTOR_VOLTAGE"]["evaluation"]["result"] == "FAIL"
    assert "WORK_INSTRUCTION:PACKAGE_BATTERY" in impact["unaffected_nodes"]
    assert impact["baseline_hash_before"] != impact["baseline_hash_after"]

    # 9. BLOCKED; prior approval preserved but STALE
    p = api.project(pid)
    assert p["status"] == "BLOCKED" and p["valid_approval_id"] is None
    approvals = api.get("approver", f"/projects/{pid}/approvals").json()["data"]
    assert [a["status"] for a in approvals] == ["STALE"] and approvals[0]["decision"] == "APPROVE"
    assert len(api.exceptions(pid)) == 5
    # R1 unchanged (AT-02)
    assert api.get("engineer", f"/projects/{pid}/bom/revisions/1").json()["data"] == {**r1, "is_current": False}
    d = api.get("engineer", f"/projects/{pid}/bom/diff", params={"from": 1, "to": 2}).json()["data"]
    assert [x["line_key"] for x in d["lines"] if x["change_type"] != "UNCHANGED"] == ["ACTUATOR_MAIN"]

    # 10. customer cannot override; compat cannot be resolved while rule FAILs
    assert (
        api.post(
            "customer", f"/projects/{pid}/approvals/request", {"expected_baseline_version": p["baseline_version"]}
        ).status_code
        == 403
    )
    r = api.resolve(pid, "COMPATIBILITY_CHECK:MOTOR_VOLTAGE", {})
    assert r.status_code == 422 and r.json()["code"] == "RESOLUTION_EVIDENCE_INVALID"
    assert api.resolve(pid, "SUPPLIER_SELECTION:ACTUATOR_MAIN", {"supplier_option_code": "SUP-201"}).status_code == 422
    assert api.resolve(pid, "SUPPLIER_SELECTION:ACTUATOR_MAIN", {"supplier_option_code": "SUP-200"}).status_code == 200

    # EC-002 (R3): bring controller/battery/chassis to the 48V / M6-50 platform
    c2 = api.propose(pid, EC002)
    assert c2.status_code == 201
    assert api.publish(pid, c2.json()["data"]["id"]).status_code == 200
    assert api.project(pid)["current_revision_number"] == 3
    for node in (
        "COMPATIBILITY_CHECK:MOTOR_VOLTAGE",
        "COMPATIBILITY_CHECK:ACTUATOR_MOUNT",
        "COMPATIBILITY_CHECK:POWER_SUPPLY",
    ):
        assert api.resolve(pid, node, {}).status_code == 200, node
    assert api.resolve(pid, "SUPPLIER_SELECTION:CONTROLLER", {"supplier_option_code": "SUP-CTRL-48"}).status_code == 200
    # QC before WI fails deterministically (QC pins WI revision, which is not yet valid)
    assert api.resolve(pid, "QC_PLAN:MOTOR_FUNCTION_TEST", qc_r3(1)).status_code == 422
    assert api.resolve(pid, "WORK_INSTRUCTION:INSTALL_ACTUATOR", WI_INSTALL_R3).status_code == 200
    assert api.resolve(pid, "WORK_INSTRUCTION:PACKAGE_BATTERY", WI_BATTERY_R3).status_code == 200
    wi_rev = api.artifact(pid, "WORK_INSTRUCTION:INSTALL_ACTUATOR")["revision"]
    assert api.resolve(pid, "QC_PLAN:MOTOR_FUNCTION_TEST", qc_r3(wi_rev)).status_code == 200
    assert api.exceptions(pid) == []
    p = api.project(pid)
    assert p["status"] == "REVIEW_REQUIRED"

    # Re-approval of the exact new baseline
    a2 = api.request_approval(pid).json()["data"]
    assert a2["bom_revision_number"] == 3 and a2["baseline_hash"] != a1["baseline_hash"]
    assert api.approve(pid, a1["id"]).status_code == 409  # stale R1 approval can never release R3
    assert api.approve(pid, a2["id"]).status_code == 200
    assert api.project(pid)["status"] == "READY"

    # AT-13: audit reconstructs the change/approval story, in order
    rows, page = [], 1
    while True:
        body = api.get("admin", f"/projects/{pid}/audit", params={"page_size": 100, "page": page}).json()
        rows += body["data"]
        if len(rows) >= body["meta"]["total"]:
            break
        page += 1
    events = {"data": rows}
    seqs = [e["seq"] for e in rows]
    assert seqs == sorted(seqs, reverse=True) and len(set(seqs)) == len(seqs)
    actions = [e["action"] for e in reversed(rows)]
    must = [
        "PROJECT_CREATED",
        "DOCUMENT_UPLOADED",
        "EXTRACTION_COMPLETED",
        "BOM_REVISION_PUBLISHED",
        "EXTRACTION_REVIEWED",
        "PLAN_TEMPLATE_APPLIED",
        "APPROVAL_REQUESTED",
        "APPROVAL_DECIDED",
        "CHANGE_PROPOSED",
        "BOM_REVISION_PUBLISHED",
        "ARTIFACT_INVALIDATED",
        "EXCEPTION_OPENED",
        "APPROVAL_STALED",
        "CHANGE_PUBLISHED",
        "EXCEPTION_RESOLVED",
        "APPROVAL_REQUESTED",
        "APPROVAL_DECIDED",
    ]
    it = iter(actions)
    assert all(m in it for m in must), "\n".join(actions)
    assert actions.count("BOM_REVISION_PUBLISHED") == 3
    decided = [e for e in events["data"] if e["action"] == "APPROVAL_DECIDED"]
    assert all(e["after_json"]["baseline_hash"] and e["after_json"]["bom_revision_id"] for e in decided)
    readiness_changes = [
        e["after_json"]["status"] for e in reversed(events["data"]) if e["action"] == "PROJECT_READINESS_CHANGED"
    ]
    assert "BLOCKED" in readiness_changes and readiness_changes[-1] == "READY"


def test_immutability_enforced_in_database(api):
    pid = api.r1_with_plan()
    with session_factory()() as db:
        for stmt in (
            "UPDATE bom_items SET quantity = 99",
            "DELETE FROM audit_events",
            "UPDATE audit_events SET action = 'X'",
            "UPDATE bom_revisions SET content_hash = 'x'",
        ):
            try:
                db.execute(text(stmt))
                raise AssertionError(f"{stmt} should be rejected")
            except Exception as e:  # noqa: BLE001
                assert "append-only" in str(e) or "immutable" in str(e), str(e)
            db.rollback()
    assert api.project(pid)["current_revision_number"] == 1


def test_api_exposes_no_audit_or_revision_mutation():
    from app.main import app

    for path, ops in app.openapi()["paths"].items():
        if "audit" in path or "/bom/revisions" in path:
            assert set(ops) <= {"get"}, path
        assert not ({"put", "delete"} & set(ops)), path
