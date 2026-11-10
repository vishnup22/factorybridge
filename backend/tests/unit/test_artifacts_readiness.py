from app.domain.artifacts import ArtifactFacts, SupplierFacts, ValidationContext, validate_artifact
from app.domain.readiness import ReadinessFacts, compute_readiness
from tests.fixtures import r1_lines, r2_lines, rules

SUP = {
    "s100": SupplierFacts("s100", "ACT-100", "A", "Synthetic Motion Supply", 500, 14, True),
    "s200": SupplierFacts("s200", "ACT-200", "B", "Synthetic Drive Works", 300, 21, True),
    "s201": SupplierFacts("s201", "ACT-200", "B", "Unvetted Parts Broker", 1000, 5, False),
}


def ctx(lines, **kw):
    return ValidationContext(
        lines={line.line_key: line for line in lines}, target_quantity=100, suppliers=SUP, rules=rules(), **kw
    )


def test_supplier_selection_valid_then_stale_after_part_change():
    content = {"line_key": "ACTUATOR_MAIN", "supplier_option_id": "s100"}
    assert validate_artifact("SUPPLIER_SELECTION", content, ctx(r1_lines())).valid
    res = validate_artifact("SUPPLIER_SELECTION", content, ctx(r2_lines()))
    assert not res.valid and "SUPPLIER_PART_MISMATCH" in {f.code for f in res.findings}


def test_unapproved_supplier_rejected_even_with_stock():
    res = validate_artifact(
        "SUPPLIER_SELECTION", {"line_key": "ACTUATOR_MAIN", "supplier_option_id": "s201"}, ctx(r2_lines())
    )
    assert "SUPPLIER_NOT_APPROVED" in {f.code for f in res.findings}


def test_insufficient_quantity():
    small = {"s": SupplierFacts("s", "ACT-200", "B", "Tiny", 10, 1, True)}
    c = ValidationContext(lines={line.line_key: line for line in r2_lines()}, target_quantity=100, suppliers=small)
    res = validate_artifact("SUPPLIER_SELECTION", {"line_key": "ACTUATOR_MAIN", "supplier_option_id": "s"}, c)
    assert "SUPPLIER_INSUFFICIENT_QTY" in {f.code for f in res.findings}


WI = {
    "document_code": "WI-ACT-01",
    "steps": ["Fasten actuator"],
    "references": {
        "ACTUATOR_MAIN": {"part_number": "ACT-100", "part_revision": "A", "attributes": {"mount_pattern": "M4-40"}}
    },
}


def test_work_instruction_reference_freshness():
    assert validate_artifact("WORK_INSTRUCTION", WI, ctx(r1_lines())).valid
    res = validate_artifact("WORK_INSTRUCTION", WI, ctx(r2_lines()))
    assert not res.valid and {f.code for f in res.findings} == {"REFERENCE_STALE"}


def test_work_instruction_requires_steps_and_references():
    res = validate_artifact("WORK_INSTRUCTION", {"document_code": "X", "steps": []}, ctx(r1_lines()))
    assert {"FIELD_REQUIRED", "REFERENCES_REQUIRED"} <= {f.code for f in res.findings}


def test_qc_plan_rejects_stale_work_instruction_ref():
    qc = {
        "document_code": "QC",
        "checks": [{"name": "v", "expected": 24}],
        "references": {"ACTUATOR_MAIN": {"part_number": "ACT-100"}},
        "artifact_refs": {"WORK_INSTRUCTION:INSTALL_ACTUATOR": 1},
    }
    fresh = ctx(r1_lines(), artifacts={"WORK_INSTRUCTION:INSTALL_ACTUATOR": ArtifactFacts("w", 1, "VALID")})
    assert validate_artifact("QC_PLAN", qc, fresh).valid
    stale = ctx(r1_lines(), artifacts={"WORK_INSTRUCTION:INSTALL_ACTUATOR": ArtifactFacts("w", 2, "VALID")})
    assert "ARTIFACT_REF_STALE" in {f.code for f in validate_artifact("QC_PLAN", qc, stale).findings}
    invalid = ctx(
        r1_lines(), artifacts={"WORK_INSTRUCTION:INSTALL_ACTUATOR": ArtifactFacts("w", 1, "REVALIDATION_REQUIRED")}
    )
    assert "ARTIFACT_REF_NOT_VALID" in {f.code for f in validate_artifact("QC_PLAN", qc, invalid).findings}


def test_qc_check_expectation_must_match_pinned_attribute():
    wi = {"WORK_INSTRUCTION:INSTALL_ACTUATOR": ArtifactFacts("w", 2, "VALID")}
    qc = {
        "document_code": "QC-ACT-01",
        "checks": [
            {"name": "Supply voltage", "parameter": "voltage_v", "expected": 24},
            {"name": "Spin", "expected": "PASS"},
        ],
        "references": {
            "ACTUATOR_MAIN": {"part_number": "ACT-200", "part_revision": "B", "attributes": {"voltage_v": 48}}
        },
        "artifact_refs": {"WORK_INSTRUCTION:INSTALL_ACTUATOR": 2},
    }
    res = validate_artifact("QC_PLAN", qc, ctx(r2_lines(), artifacts=wi))
    assert {f.code for f in res.findings} == {"QC_CHECK_STALE"}
    qc["checks"][0]["expected"] = 48.0
    assert validate_artifact("QC_PLAN", qc, ctx(r2_lines(), artifacts=wi)).valid


def test_compatibility_artifact_computed_server_side():
    ok = validate_artifact("COMPATIBILITY_CHECK", {"rule_code": "MOTOR_VOLTAGE", "result": "PASS"}, ctx(r2_lines()))
    assert not ok.valid and ok.computed_content["result"] == "FAIL"  # client-claimed PASS ignored
    unknown = validate_artifact("COMPATIBILITY_CHECK", {"rule_code": "NOPE"}, ctx(r1_lines()))
    assert not unknown.valid and unknown.findings[0].code == "COMPATIBILITY_UNKNOWN"


def facts(**kw):
    base = {
        "has_bom": True,
        "pending_extractions": 0,
        "graph_node_count": 3,
        "required_nodes": ["QC_PLAN:X"],
        "artifact_status": {"QC_PLAN:X": "VALID"},
        "open_exceptions": [],
        "current_baseline_hash": "h1",
        "current_baseline_version": 3,
        "approved": [],
    }
    base.update(kw)
    return ReadinessFacts(**base)


def test_readiness_states():
    assert compute_readiness(facts(has_bom=False)).status == "DRAFT"
    assert compute_readiness(facts()).status == "REVIEW_REQUIRED"
    assert compute_readiness(facts(approved=[("a", "h1", 3)])).status == "READY"
    assert compute_readiness(facts(approved=[("a", "h0", 3)])).status == "REVIEW_REQUIRED"  # wrong hash
    assert compute_readiness(facts(approved=[("a", "h1", 2)])).status == "REVIEW_REQUIRED"  # wrong version
    assert (
        compute_readiness(facts(approved=[("a", "h1", 3)], open_exceptions=[("e", "CRITICAL", "X")])).status
        == "BLOCKED"
    )
    assert (
        compute_readiness(
            facts(approved=[("a", "h1", 3)], artifact_status={"QC_PLAN:X": "REVALIDATION_REQUIRED"})
        ).status
        == "BLOCKED"
    )
    assert compute_readiness(facts(artifact_status={})).status == "BLOCKED"
    assert compute_readiness(facts(graph_node_count=0, required_nodes=[])).status == "BLOCKED"
    assert compute_readiness(facts(pending_extractions=1)).status == "EXTRACTING"
    # LOW/MEDIUM exceptions do not block
    assert compute_readiness(facts(approved=[("a", "h1", 3)], open_exceptions=[("e", "MEDIUM", "X")])).status == "READY"
