import json
import random
import time

from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.graph import Edge, find_cycle, traverse_downstream
from app.domain.impact import assess_impact
from app.domain.rules import evaluate
from tests.fixtures import EC002, FIXTURES, apply_change_set, artifact_nodes, plan_edges, r1_lines, r2_lines, rules


def _by_node(result):
    return {i.node: i for i in result.impacted}


def test_ec001_matches_expected_impact_fixture():
    expected = json.loads((FIXTURES / "expected_impact.json").read_text())
    result = assess_impact(r1_lines(), r2_lines(), plan_edges(), rules(), artifact_nodes())
    got = _by_node(result)
    assert result.changed_nodes == expected["changed_nodes"]
    for exp in expected["impacted"]:
        node = got[exp["node"]]
        assert node.severity == exp["severity"], exp["node"]
        assert node.reason_code == exp["reason_code"], exp["node"]
        if "evaluation" in exp:
            assert node.evaluation and node.evaluation["result"] == exp["evaluation"]
    assert set(got) == {e["node"] for e in expected["impacted"]}
    for n in expected["must_not_be_impacted"]:
        assert n not in got
    assert {"SUPPLIER_SELECTION", "COMPATIBILITY_CHECK", "WORK_INSTRUCTION", "QC_PLAN", "READINESS_APPROVAL"} <= {
        i.node_type for i in result.impacted
    }


def test_impact_paths_explain_dependency():
    got = _by_node(assess_impact(r1_lines(), r2_lines(), plan_edges(), rules(), artifact_nodes()))
    assert got["SUPPLIER_SELECTION:ACTUATOR_MAIN"].path == (
        "BOM_LINE:ACTUATOR_MAIN",
        "SUPPLIER_SELECTION:ACTUATOR_MAIN",
    )
    assert got["COMPATIBILITY_CHECK:MOTOR_VOLTAGE"].rule_codes == ("MOTOR_VOLTAGE",)
    assert "attributes.voltage_v" in got["COMPATIBILITY_CHECK:MOTOR_VOLTAGE"].changed_fields


def test_impact_is_deterministic_on_rerun():
    a = assess_impact(r1_lines(), r2_lines(), plan_edges(), rules(), artifact_nodes()).as_dict()
    shuffled = plan_edges()
    random.Random(7).shuffle(shuffled)
    b = assess_impact(r1_lines(), r2_lines(), shuffled, rules(), artifact_nodes()).as_dict()
    assert a == b


def test_voltage_rule_is_deterministic_fail_for_act200():
    lines = {line.line_key: line for line in r2_lines()}
    res = evaluate(rules()["MOTOR_VOLTAGE"], lines)
    assert res.result == "FAIL" and res.reason_code == "VOLTAGE_COMPATIBILITY_CHANGED"
    assert res.evidence["left"]["value"] == 48 and res.evidence["right"]["value"] == 24
    assert evaluate(rules()["ACTUATOR_MOUNT"], lines).result == "FAIL"
    assert evaluate(rules()["POWER_SUPPLY"], lines).result == "PASS"


def test_missing_attribute_is_unknown_not_pass():
    lines = {line.line_key: line for line in r1_lines()}
    ctrl = lines["CONTROLLER"]
    lines["CONTROLLER"] = type(ctrl)(**{**ctrl.__dict__, "attributes": {"connector": "JST-X"}})
    res = evaluate(rules()["MOTOR_VOLTAGE"], lines)
    assert res.result == "UNKNOWN" and res.reason_code == "COMPATIBILITY_UNKNOWN"


def test_r3_resolution_passes_all_rules():
    r3 = {line.line_key: line for line in apply_change_set(r2_lines(), EC002)}
    assert {code: evaluate(rule, r3).result for code, rule in rules().items()} == {
        "MOTOR_VOLTAGE": "PASS",
        "ACTUATOR_MOUNT": "PASS",
        "POWER_SUPPLY": "PASS",
    }


def test_missing_artifact_record_is_critical():
    nodes = artifact_nodes() - {"WORK_INSTRUCTION:INSTALL_ACTUATOR"}
    got = _by_node(assess_impact(r1_lines(), r2_lines(), plan_edges(), rules(), nodes))
    assert got["WORK_INSTRUCTION:INSTALL_ACTUATOR"].reason_code == "MANDATORY_FIELD_MISSING"
    assert got["WORK_INSTRUCTION:INSTALL_ACTUATOR"].severity == "CRITICAL"


def test_unregistered_rule_is_compatibility_unknown():
    r = rules()
    del r["MOTOR_VOLTAGE"]
    got = _by_node(assess_impact(r1_lines(), r2_lines(), plan_edges(), r, artifact_nodes()))
    assert got["COMPATIBILITY_CHECK:MOTOR_VOLTAGE"].reason_code == "COMPATIBILITY_UNKNOWN"


def test_cycle_detection():
    edges = [Edge("BOM_LINE:A", "WORK_INSTRUCTION:X", "T", "R"), Edge("WORK_INSTRUCTION:X", "QC_PLAN:Y", "T", "R")]
    assert find_cycle(edges) is None
    cycle = find_cycle([*edges, Edge("QC_PLAN:Y", "WORK_INSTRUCTION:X", "T", "R")])
    assert cycle is not None and cycle[0] == cycle[-1]
    assert find_cycle(plan_edges()) is None


def test_added_line_without_edges_still_stales_readiness():
    new = apply_change_set(
        r1_lines(),
        [
            {
                "line_key": "LIDAR",
                "operation": "ADD",
                "new": {"part_number": "LDR-1", "part_revision": "A", "quantity": 1, "unit": "each"},
            }
        ],
    )
    got = _by_node(assess_impact(r1_lines(), new, plan_edges(), rules(), artifact_nodes()))
    assert set(got) == {"READINESS_APPROVAL:PROJECT"}


@settings(max_examples=60, deadline=None)
@given(st.data())
def test_property_traversal_reachability_is_monotonic(data):
    n = data.draw(st.integers(min_value=2, max_value=25))
    nodes = [f"QC_PLAN:N{i:02d}" for i in range(n)]
    pairs = data.draw(st.lists(st.tuples(st.integers(0, n - 1), st.integers(0, n - 1)), max_size=60))
    edges = [Edge(nodes[a], nodes[b], "T", "R") for a, b in pairs if a < b]  # forward-only => acyclic
    assert find_cycle(edges) is None
    seed = nodes[0]
    base = {r.node for r in traverse_downstream(edges, [seed])}
    extra = Edge(nodes[0], nodes[-1], "T2", "R")
    more = {r.node for r in traverse_downstream([*edges, extra], [seed])}
    assert base <= more
    again = traverse_downstream(list(reversed(edges)), [seed])
    assert [r.node for r in again] == [r.node for r in traverse_downstream(edges, [seed])]


def test_large_dag_impact_benchmark_records_actual_time():
    """2000-node / ~5000-edge DAG. Asserts a generous bound and prints the measured time; the
    reported figure in TEST_RESULTS.md comes from this actual run, not an estimate."""
    rng = random.Random(42)
    nodes = [f"QC_PLAN:N{i:04d}" for i in range(2000)]
    edges = [Edge("BOM_LINE:ROOT", nodes[0], "T", "R")]
    while len(edges) < 5000:
        a, b = sorted(rng.sample(range(2000), 2))
        edges.append(Edge(nodes[a], nodes[b], "T", "R"))
    started = time.perf_counter()
    assert find_cycle(edges) is None
    reach = traverse_downstream(edges, ["BOM_LINE:ROOT"])
    elapsed = time.perf_counter() - started
    print(f"\nBENCH dag nodes=2000 edges={len(edges)} reached={len(reach)} cycle_check+bfs_seconds={elapsed:.4f}")
    assert elapsed < 2.0
