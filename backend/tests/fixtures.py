"""Shared synthetic fixtures for unit tests (mirrors demo/fixtures)."""

import json
from pathlib import Path

from app.domain.bom import BomLine, apply_change_set, validate_bom
from app.domain.graph import READINESS_NODE, Edge, bom_node
from app.domain.rules import Rule

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "demo" / "fixtures"

R1_RAW = [
    {
        "line_key": "ACTUATOR_MAIN",
        "part_number": "ACT-100",
        "part_revision": "A",
        "description": "Synthetic drive actuator",
        "quantity": 2,
        "unit": "each",
        "attributes": {"voltage_v": 24, "mount_pattern": "M4-40", "connector": "JST-X"},
    },
    {
        "line_key": "CONTROLLER",
        "part_number": "CTRL-24",
        "part_revision": "A",
        "description": "Synthetic motor controller",
        "quantity": 1,
        "unit": "each",
        "attributes": {"output_voltage_v": 24, "input_voltage_v": 24, "connector": "JST-X"},
    },
    {
        "line_key": "BATTERY",
        "part_number": "BAT-24",
        "part_revision": "A",
        "description": "Synthetic battery pack",
        "quantity": 1,
        "unit": "each",
        "attributes": {"voltage_v": 24},
    },
    {
        "line_key": "CHASSIS",
        "part_number": "CHS-01",
        "part_revision": "A",
        "description": "Synthetic chassis frame",
        "quantity": 1,
        "unit": "each",
        "attributes": {"accepted_mounts": ["M4-40"]},
    },
    {
        "line_key": "SENSOR",
        "part_number": "SNS-02",
        "part_revision": "A",
        "description": "Synthetic proximity sensor",
        "quantity": 2,
        "unit": "each",
        "attributes": {"voltage_v": 24},
    },
]

EC001 = [
    {
        "line_key": "ACTUATOR_MAIN",
        "operation": "UPDATE",
        "new": {
            "part_number": "ACT-200",
            "description": "Synthetic 48V drive actuator",
            "quantity": 2,
            "unit": "each",
            "part_revision": "B",
            "attributes": {"voltage_v": 48, "mount_pattern": "M6-50", "connector": "JST-X"},
        },
    }
]

EC002 = [
    {
        "line_key": "CONTROLLER",
        "operation": "UPDATE",
        "new": {
            "part_number": "CTRL-48",
            "part_revision": "A",
            "description": "Synthetic 48V motor controller",
            "attributes": {"output_voltage_v": 48, "input_voltage_v": 48, "connector": "JST-X"},
        },
    },
    {
        "line_key": "BATTERY",
        "operation": "UPDATE",
        "new": {
            "part_number": "BAT-48",
            "part_revision": "A",
            "description": "Synthetic 48V battery pack",
            "attributes": {"voltage_v": 48},
        },
    },
    {
        "line_key": "CHASSIS",
        "operation": "UPDATE",
        "new": {
            "part_number": "CHS-02",
            "part_revision": "A",
            "description": "Synthetic chassis frame (dual mount)",
            "attributes": {"accepted_mounts": ["M4-40", "M6-50"]},
        },
    },
]


def r1_lines() -> list[BomLine]:
    lines, issues = validate_bom(R1_RAW)
    assert not issues
    return lines


def r2_lines() -> list[BomLine]:
    return apply_change_set(r1_lines(), EC001)


def rules() -> dict[str, Rule]:
    raw = json.loads((FIXTURES / "compatibility_rules.json").read_text())["rules"]
    return {r["rule_code"]: Rule(r["rule_code"], r["check_kind"], r["params"], r["is_critical"]) for r in raw}


def plan_edges() -> list[Edge]:
    plan = json.loads((FIXTURES / "dependency_plan.json").read_text())
    edges = [Edge(e["source"], e["target"], e["dependency_type"], e["rule_code"]) for e in plan["edges"]]
    for rule in rules().values():
        edges += [
            Edge(bom_node(k), f"COMPATIBILITY_CHECK:{rule.rule_code}", "COMPATIBILITY_INPUT", rule.rule_code)
            for k in rule.line_keys()
        ]
    edges += [Edge(bom_node(r["line_key"]), READINESS_NODE, "RELEASE_BASELINE", "REL-BASELINE") for r in R1_RAW]
    return edges


def artifact_nodes() -> set[str]:
    plan = json.loads((FIXTURES / "dependency_plan.json").read_text())
    return {a["node_key"] for a in plan["artifacts"]}
