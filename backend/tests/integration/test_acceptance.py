"""Acceptance tests from FACTORYBRIDGE_MASTER_SPEC.md section 8 (AT-01..AT-12; AT-13 in test_flagship,
AT-08/AT-09 in test_reliability, AT-14 config guard in test_security)."""

import uuid

from app.services.extraction.providers import PROMPT_VERSION
from app.services.extraction.schema import BomCandidate, Evidence, ExtractionOutput, ExtractionResult, ModelMetadata
from tests.fixtures import EC001
from tests.integration.conftest import FIXTURES

SENSOR_TWEAK = [{"line_key": "SENSOR", "operation": "UPDATE", "new": {"description": "Synthetic proximity sensor v2"}}]


class ScriptedProvider:
    """Model stand-in returning a fixed structured output (no network)."""

    mode = "LIVE"
    model = "scripted-test-model"

    def __init__(self, candidates: list[BomCandidate]):
        self.candidates = candidates

    def extract(self, pages: list[str], request_id: str) -> ExtractionResult:
        return ExtractionResult(
            output=ExtractionOutput(product_sku="WS-100", target_quantity=100, bom_candidates=self.candidates),
            metadata=ModelMetadata(
                provider_mode="LIVE",
                model=self.model,
                prompt_version=PROMPT_VERSION,
                input_tokens=1000,
                output_tokens=200,
                latency_ms=5,
            ),
        )


# ---------------- AT-01: R1 only from reviewed input ----------------
def test_at01_r1_created_only_after_human_review(api):
    pid = api.create_project()
    doc = api.upload(pid, "requirements_v1.pdf").json()["data"]
    api.run_worker()
    assert api.project(pid)["current_revision_number"] is None
    ex = api.get("engineer", f"/documents/{doc['id']}/extraction").json()["data"]
    assert ex["run"]["provider_mode"] == "FIXTURE" and ex["run"]["status"] == "REVIEW_REQUIRED"
    bom = [c for c in ex["candidates"] if c["kind"] == "BOM_LINE"]
    reqs = [c for c in ex["candidates"] if c["kind"] == "REQUIREMENT"]
    assert len(bom) == 5 and len(reqs) == 3
    assert all(c["source_page"] == 2 and c["evidence_status"] == "VERIFIED" for c in bom)

    # no change can be proposed before an R1 exists
    assert api.propose(pid, SENSOR_TWEAK, base=1).status_code in (404, 409, 422)
    # partial review is refused; customers cannot accept
    partial = [{"candidate_id": bom[0]["id"], "action": "ACCEPT"}]
    r = api.post("engineer", f"/documents/{doc['id']}/extraction/accept", {"decisions": partial, "reason": "partial"})
    assert r.status_code == 422 and r.json()["code"] == "UNDECIDED_CANDIDATES"
    assert (
        api.post(
            "customer", f"/documents/{doc['id']}/extraction/accept", {"decisions": [], "reason": "nope"}
        ).status_code
        == 403
    )
    # EDIT requires a note; REJECT of a requirement is allowed
    decisions = [{"candidate_id": c["id"], "action": "ACCEPT"} for c in bom]
    decisions[4] = {"candidate_id": bom[4]["id"], "action": "EDIT", "value": {**bom[4]["value_json"], "quantity": 3}}
    decisions += [{"candidate_id": c["id"], "action": "REJECT"} for c in reqs]
    r = api.post("engineer", f"/documents/{doc['id']}/extraction/accept", {"decisions": decisions, "reason": "review"})
    assert r.status_code == 422
    decisions[4]["note"] = "Customer confirmed 3 sensors by email (synthetic)"
    r = api.post("engineer", f"/documents/{doc['id']}/extraction/accept", {"decisions": decisions, "reason": "review"})
    assert r.status_code == 200 and r.json()["data"]["bom_revision"] == 1
    items = {i["line_key"]: i for i in api.get("engineer", f"/projects/{pid}/bom/revisions/1").json()["data"]["items"]}
    assert items["SENSOR"]["quantity"] == 3
    assert items["SENSOR"]["provenance_json"]["review_action"] == "EDIT"
    assert items["ACTUATOR_MAIN"]["provenance_json"]["page"] == 2
    # accepting twice cannot create a second baseline
    again = api.post(
        "engineer", f"/documents/{doc['id']}/extraction/accept", {"decisions": decisions, "reason": "again"}
    )
    assert again.status_code == 409
    assert api.project(pid)["current_revision_number"] == 1


def test_second_document_creates_proposed_change_not_overwrite(api):
    pid, _ = api.ready_r1()
    doc = api.upload(pid, "requirements_v2.pdf").json()["data"]
    api.run_worker()
    r = api.accept_all(doc["id"])
    assert r.status_code == 200
    out = r.json()["data"]
    assert out["bom_revision"] is None and out["change_code"] == "EC-001"
    p = api.project(pid)
    assert p["current_revision_number"] == 1 and p["status"] == "READY"
    impact = api.get("engineer", f"/projects/{pid}/changes/{out['change_id']}/impact").json()["data"]
    assert impact["persisted"] is False and "SUPPLIER_SELECTION:ACTUATOR_MAIN" in {
        i["node"] for i in impact["impacted"]
    }


# ---------------- AT-02: revision immutability, diff and hash ----------------
def test_at02_revisions_immutable_diff_and_hash(api):
    pid = api.r1_with_plan()
    r1 = api.get("engineer", f"/projects/{pid}/bom/revisions/1").json()["data"]
    cid = api.propose(pid, EC001).json()["data"]["id"]
    assert api.publish(pid, cid).status_code == 200
    revs = api.get("engineer", f"/projects/{pid}/bom/revisions").json()["data"]
    assert [r["revision_number"] for r in revs] == [1, 2]
    assert revs[1]["parent_revision_id"] == r1["id"] and revs[1]["content_hash"] != r1["content_hash"]
    assert api.get("engineer", f"/projects/{pid}/bom/revisions/1").json()["data"]["content_hash"] == r1["content_hash"]
    d = api.get("engineer", f"/projects/{pid}/bom/diff", params={"from": 1, "to": 2}).json()["data"]
    act = next(x for x in d["lines"] if x["line_key"] == "ACTUATOR_MAIN")
    assert act["change_type"] == "UPDATED"
    assert {"part_number", "part_revision", "attributes.voltage_v", "attributes.mount_pattern"} <= set(
        act["changed_fields"]
    )
    # re-publishing the same content is refused
    same = api.propose(pid, EC001)
    assert same.status_code == 422 and same.json()["code"] == "INVALID_CHANGE_SET"
    assert "NO_EFFECTIVE_CHANGE" in {i["code"] for i in same.json()["extra"]["issues"]}


# ---------------- AT-03: ACT-200 impact ----------------
def test_at03_impact_matches_expected_fixture(api):
    import json

    expected = json.loads((FIXTURES / "expected_impact.json").read_text())
    pid, _ = api.ready_r1()
    cid = api.propose(pid, EC001).json()["data"]["id"]
    impact = api.publish(pid, cid).json()["data"]
    got = {i["node"]: i for i in impact["impacted"]}
    for exp in expected["impacted"]:
        assert exp["node"] in got, exp["node"]
        assert got[exp["node"]]["reason_code"] == exp["reason_code"]
        assert got[exp["node"]]["severity"] == exp["severity"]
    for node in expected["must_not_be_impacted"]:
        assert node not in got
    # persisted assessment is identical when fetched again (deterministic, stored)
    again = api.get("engineer", f"/projects/{pid}/changes/{cid}/impact").json()["data"]
    assert again["persisted"] is True and again["impacted"] == impact["impacted"]


# ---------------- AT-04: blocking conditions ----------------
def test_at04_missing_evidence_cannot_be_accepted(api):
    pid = api.create_project()
    doc = api.upload(pid, "requirements_v1.pdf").json()["data"]
    fabricated = [
        BomCandidate(
            line_key="ACTUATOR_MAIN",
            part_number="ACT-100",
            part_revision="A",
            description="Synthetic drive actuator",
            quantity=2,
            unit="each",
            attributes={"voltage_v": 24},
            evidence=Evidence(page_number=2, exact_excerpt="BOM LINE ACTUATOR_MAIN: ACT-100 rev A, qty 2 each"),
            confidence="HIGH",
        ),
        BomCandidate(
            line_key="GEARBOX",
            part_number="GBX-9",
            part_revision="A",
            description="Invented part",
            quantity=1,
            unit="each",
            evidence=Evidence(page_number=2, exact_excerpt="GEARBOX GBX-9 qty 1"),
            confidence="HIGH",
        ),
    ]
    api.run_worker(provider=ScriptedProvider(fabricated))
    ex = api.get("engineer", f"/documents/{doc['id']}/extraction").json()["data"]
    by_key = {c["field_path"]: c for c in ex["candidates"]}
    assert by_key["bom.ACTUATOR_MAIN"]["evidence_status"] == "VERIFIED"
    assert by_key["bom.GEARBOX"]["evidence_status"] != "VERIFIED"
    assert ex["run"]["cost_usd_estimate"] is not None and ex["run"]["cost_assumptions"]["note"].startswith("Configured")
    decisions = [{"candidate_id": c["id"], "action": "ACCEPT"} for c in ex["candidates"]]
    r = api.post("engineer", f"/documents/{doc['id']}/extraction/accept", {"decisions": decisions, "reason": "try"})
    assert r.status_code == 422 and r.json()["code"] == "INVALID_REVIEW_DECISIONS"
    assert api.project(pid)["current_revision_number"] is None


def test_at04_unknown_compatibility_blocks(api):
    csv = (
        (FIXTURES / "bom_v1.csv")
        .read_text()
        .replace("CHS-01,A,Synthetic chassis frame,1,each,,,,,M4-40,", "CHS-01,A,Synthetic chassis frame,1,each,,,,,,")
    )
    pid = api.create_project()
    doc = api.upload(pid, "bom_no_mounts.csv", data=csv.encode()).json()["data"]
    api.run_worker()
    assert api.accept_all(doc["id"]).status_code == 200
    api.post("engineer", f"/projects/{pid}/plan/apply-template", {"template": "warehouse_scout_v1"})
    art = api.artifact(pid, "COMPATIBILITY_CHECK:ACTUATOR_MOUNT")
    assert art["status"] == "INVALID"
    assert "COMPATIBILITY_UNKNOWN" in {f["code"] for f in art["validation_json"]["findings"]}
    p = api.project(pid)
    assert p["status"] == "BLOCKED"
    assert any(
        b["code"] == "ARTIFACT_NOT_VALID" and b["ref"] == "COMPATIBILITY_CHECK:ACTUATOR_MOUNT" for b in p["blockers"]
    )
    r = api.request_approval(pid)
    assert r.status_code == 422 and r.json()["code"] == "BLOCKERS_PRESENT"


def test_at04_unreviewed_critical_exception_blocks_and_cannot_be_waived(api):
    pid, _ = api.ready_r1()
    cid = api.propose(pid, EC001).json()["data"]["id"]
    api.publish(pid, cid)
    r = api.request_approval(pid)
    assert r.status_code == 422 and r.json()["code"] == "BLOCKERS_PRESENT"
    crit = api.exc_for(pid, "COMPATIBILITY_CHECK:MOTOR_VOLTAGE")
    assert crit["severity"] == "CRITICAL"
    w = api.post("approver", f"/projects/{pid}/exceptions/{crit['id']}/waive", {"rationale": "Ship it anyway please"})
    assert w.status_code == 422 and w.json()["code"] == "NOT_WAIVABLE"
    high = api.exc_for(pid, "WORK_INSTRUCTION:INSTALL_ACTUATOR")
    assert (
        api.post(
            "engineer", f"/projects/{pid}/exceptions/{high['id']}/waive", {"rationale": "Engineer cannot waive"}
        ).status_code
        == 403
    )
    ok = api.post(
        "approver",
        f"/projects/{pid}/exceptions/{high['id']}/waive",
        {"rationale": "Synthetic: WI covered by interim supervisor sign-off"},
    )
    assert ok.status_code == 200 and ok.json()["data"]["status"] == "WAIVED"
    assert api.project(pid)["status"] == "BLOCKED"


# ---------------- AT-05: customer cannot approve ----------------
def test_at05_customer_cannot_approve(api):
    pid = api.r1_with_plan()
    aid = api.request_approval(pid).json()["data"]["id"]
    r = api.approve(pid, aid, who="customer")
    assert r.status_code == 403 and r.json()["code"] == "ROLE_FORBIDDEN"
    r = api.approve(pid, aid, who="engineer")
    assert r.status_code == 403
    assert api.project(pid)["status"] == "REVIEW_REQUIRED"
    audit = api.get("admin", f"/projects/{pid}/audit", params={"action": "APPROVAL_DECIDED"}).json()["data"]
    assert audit == []


# ---------------- AT-06: change author cannot approve ----------------
def test_at06_change_author_cannot_approve_own_change(api):
    pid, _ = api.ready_r1()
    ch = api.propose(pid, SENSOR_TWEAK, who="approver")
    assert ch.status_code == 201
    assert api.publish(pid, ch.json()["data"]["id"]).status_code == 200
    p = api.project(pid)
    assert p["status"] == "REVIEW_REQUIRED"
    users = {u["email"]: u["id"] for u in api.get("admin", "/admin/users").json()["data"]}
    r = api.request_approval(pid, assigned_approver_id=users["approver@demo.example"])
    assert r.status_code == 422 and r.json()["code"] == "APPROVER_INELIGIBLE"
    a = api.request_approval(pid).json()["data"]
    assert a["assigned_approver_email"] == "approver2@demo.example"
    r = api.approve(pid, a["id"], who="approver")
    assert r.status_code == 403
    assert api.approve(pid, a["id"], who="approver2").status_code == 200
    assert api.project(pid)["status"] == "READY"


def test_at06_engineer_who_requested_cannot_be_approver_even_if_promoted(api):
    pid = api.r1_with_plan()
    a = api.request_approval(pid).json()["data"]
    users = {u["email"]: u["id"] for u in api.get("admin", "/admin/users").json()["data"]}
    assert (
        api.client.patch(
            f"/api/v1/admin/users/{users['engineer@demo.example']}", json={"role": "approver"}, headers=api.h("admin")
        ).status_code
        == 200
    )
    r = api.approve(pid, a["id"], who="engineer")
    assert r.status_code == 403


# ---------------- AT-07: stale approval ----------------
def test_at07_stale_approval_rejected(api):
    pid = api.r1_with_plan()
    a = api.request_approval(pid).json()["data"]
    cid = api.propose(pid, EC001).json()["data"]["id"]
    assert api.publish(pid, cid).status_code == 200
    r = api.approve(pid, a["id"])
    assert r.status_code == 409 and r.json()["code"] == "APPROVAL_STALE"
    p = api.project(pid)
    assert p["status"] == "BLOCKED" and p["valid_approval_id"] is None


def test_at07_hash_pin_mismatch_rejected(api):
    pid = api.r1_with_plan()
    a = api.request_approval(pid).json()["data"]
    r = api.approve(pid, a["id"], expected_baseline_hash="0" * 64)
    assert r.status_code == 409 and r.json()["code"] == "REVIEWED_BASELINE_MISMATCH"
    assert api.project(pid)["status"] == "REVIEW_REQUIRED"


def test_rejection_keeps_project_blocked_from_release(api):
    pid = api.r1_with_plan()
    a = api.request_approval(pid).json()["data"]
    assert api.approve(pid, a["id"], decision="REJECT").status_code == 200
    p = api.project(pid)
    assert p["status"] == "REVIEW_REQUIRED" and p["valid_approval_id"] is None


# ---------------- AT-10: idempotency ----------------
def test_at10_idempotency_publish_and_decision(api):
    pid = api.r1_with_plan()
    cid = api.propose(pid, SENSOR_TWEAK).json()["data"]["id"]
    k = {"Idempotency-Key": str(uuid.uuid4())}
    r1 = api.publish(pid, cid, headers=k)
    r2 = api.publish(pid, cid, headers=k)
    assert r1.status_code == r2.status_code == 200
    assert r2.headers.get("Idempotent-Replayed") == "true" and r1.json() == r2.json()
    assert api.project(pid)["current_revision_number"] == 2
    r3 = api.publish(pid, cid, headers=k, expected_baseline_version=1)
    assert r3.status_code == 409 and r3.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    # without the key, a second publish is a conflict, not a duplicate
    assert api.publish(pid, cid).status_code == 409

    a = api.request_approval(pid).json()["data"]
    k2 = {"Idempotency-Key": str(uuid.uuid4())}
    d1 = api.approve(pid, a["id"], headers=k2)
    d2 = api.approve(pid, a["id"], headers=k2)
    assert d1.status_code == d2.status_code == 200 and d2.headers.get("Idempotent-Replayed") == "true"
    decided = api.get("admin", f"/projects/{pid}/audit", params={"action": "APPROVAL_DECIDED"}).json()["data"]
    assert len(decided) == 1
    # keys are scoped per user: another user's identical key is independent
    assert api.approve(
        pid,
        a["id"],
        who="approver2" if a["assigned_approver_email"].startswith("approver@") else "approver",
        headers=k2,
    ).status_code in (403, 409)


def test_at10_duplicate_upload_returns_existing_document(api):
    pid = api.create_project()
    a = api.upload(pid, "bom_v1.csv")
    b = api.upload(pid, "bom_v1.csv")
    assert a.status_code == 202 and b.status_code == 200
    assert a.json()["data"]["id"] == b.json()["data"]["id"]
    assert api.run_worker() == 1
    assert len(api.get("engineer", f"/projects/{pid}/documents").json()["data"]) == 1


# ---------------- AT-11: malformed / scanned / disguised documents ----------------
def test_at11_malformed_pdf_fails_without_fabrication(api):
    pid = api.create_project()
    for name, code in (("malformed.pdf", "PDF_MALFORMED"), ("scanned_no_text.pdf", "PDF_NO_TEXT")):
        up = api.upload(pid, name)
        assert up.status_code == 202, up.text
        doc_id = up.json()["data"]["id"]
        api.run_worker()
        d = api.get("engineer", f"/documents/{doc_id}").json()["data"]
        assert d["processing_status"] == "FAILED" and d["error_code"] == code
        ex = api.get("engineer", f"/documents/{doc_id}/extraction").json()["data"]
        assert ex["run"]["status"] == "FAILED" and ex["candidates"] == []
        assert d["job"]["status"] == "FAILED" and d["job"]["attempt"] == 1  # permanent: no retries
    assert api.project(pid)["current_revision_number"] is None


def test_at11_disguised_and_unsupported_uploads_rejected(api):
    pid = api.create_project()
    r = api.upload(pid, "invoice.pdf", data=b"MZ\x90\x00 definitely not a pdf")
    assert r.status_code == 422 and r.json()["code"] in ("UNSUPPORTED_MEDIA_TYPE", "PDF_MALFORMED")
    r = api.upload(pid, "empty.csv", data=b"")
    assert r.status_code == 422
    r = api.upload(pid, "../../etc/passwd.csv", data=(FIXTURES / "bom_v1.csv").read_bytes())
    assert r.status_code == 202
    assert "/" not in r.json()["data"]["filename"] and ".." not in r.json()["data"]["filename"]


# ---------------- AT-12 (API level): fixture-mode PDF demo path ----------------
def test_at12_fixture_mode_pdf_to_ready(api):
    pid = api.create_project()
    doc = api.upload(pid, "requirements_v1.pdf").json()["data"]
    api.run_worker()
    ex = api.get("engineer", f"/documents/{doc['id']}/extraction").json()["data"]
    decisions = [{"candidate_id": c["id"], "action": "ACCEPT"} for c in ex["candidates"]]
    assert (
        api.post(
            "engineer", f"/documents/{doc['id']}/extraction/accept", {"decisions": decisions, "reason": "Reviewed"}
        ).status_code
        == 200
    )
    api.post("engineer", f"/projects/{pid}/plan/apply-template", {"template": "warehouse_scout_v1"})
    a = api.request_approval(pid).json()["data"]
    assert api.approve(pid, a["id"]).status_code == 200
    assert api.project(pid)["status"] == "READY"
    me = api.get("engineer", "/auth/me").json()
    assert me["ai_mode"] == "FIXTURE"
