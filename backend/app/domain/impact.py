"""Change-impact assessment: stable diff -> seeds -> deterministic traversal -> reason codes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.bom import BomLine, LineDiff, diff
from app.domain.graph import READINESS_NODE, Edge, bom_node, node_type, traverse_downstream
from app.domain.rules import Rule, evaluate

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

REQUIRED_ACTIONS = {
    "SOURCING_REVALIDATION_REQUIRED": "Select an approved synthetic supplier option for the new part/revision.",
    "VOLTAGE_COMPATIBILITY_CHANGED": "Re-run the voltage rule; change BOM components until it passes.",
    "MOUNTING_INTERFACE_CHANGED": "Re-run the mounting rule; change BOM components until it passes.",
    "COMPATIBILITY_UNKNOWN": "Provide the missing attributes or register an explicit rule; UNKNOWN blocks release.",
    "WORK_INSTRUCTION_STALE": "Publish a new work-instruction revision referencing the current parts.",
    "QC_PLAN_STALE": "Publish a new QC plan revision referencing the current parts and instruction.",
    "READINESS_APPROVAL_STALE": "Request and obtain a new approval for the current baseline.",
    "MANDATORY_FIELD_MISSING": "Create the missing downstream artifact record for this dependency node.",
    "BOM_PART_CHANGED": "Review the dependent BOM line.",
}


@dataclass(frozen=True)
class ImpactedNode:
    node: str
    node_type: str
    severity: str
    reason_code: str
    path: tuple[str, ...]
    rule_codes: tuple[str, ...]
    changed_fields: tuple[str, ...]
    required_action: str
    artifact_exists: bool
    evaluation: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "node_type": self.node_type,
            "severity": self.severity,
            "reason_code": self.reason_code,
            "path": list(self.path),
            "rule_codes": list(self.rule_codes),
            "changed_fields": list(self.changed_fields),
            "required_action": self.required_action,
            "artifact_exists": self.artifact_exists,
            "evaluation": self.evaluation,
        }


@dataclass(frozen=True)
class ImpactResult:
    line_diffs: list[LineDiff]
    changed_nodes: list[str]
    impacted: list[ImpactedNode]
    unaffected_nodes: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "line_diffs": [d.as_dict() for d in self.line_diffs if d.change_type != "UNCHANGED"],
            "changed_nodes": self.changed_nodes,
            "impacted": [i.as_dict() for i in self.impacted],
            "unaffected_nodes": self.unaffected_nodes,
        }


def _classify(
    node: str, rules: dict[str, Rule], new_lines: dict[str, BomLine], has_artifact: bool
) -> tuple[str, str, dict[str, Any] | None]:
    ntype = node_type(node)
    if ntype == "READINESS_APPROVAL":
        return "READINESS_APPROVAL_STALE", "CRITICAL", None
    if ntype == "BOM_LINE":
        return "BOM_PART_CHANGED", "MEDIUM", None
    if ntype == "COMPATIBILITY_CHECK":
        rule = rules.get(node.split(":", 1)[1])
        if rule is None:
            return "COMPATIBILITY_UNKNOWN", "CRITICAL", None
        evaluation = evaluate(rule, new_lines).as_dict()
        if evaluation["result"] == "UNKNOWN":
            return "COMPATIBILITY_UNKNOWN", "CRITICAL", evaluation
        return rule.change_reason_code, ("CRITICAL" if rule.is_critical else "HIGH"), evaluation
    if not has_artifact:
        return "MANDATORY_FIELD_MISSING", "CRITICAL", None
    return {
        "SUPPLIER_SELECTION": ("SOURCING_REVALIDATION_REQUIRED", "HIGH", None),
        "WORK_INSTRUCTION": ("WORK_INSTRUCTION_STALE", "HIGH", None),
        "QC_PLAN": ("QC_PLAN_STALE", "HIGH", None),
    }.get(ntype, ("MANDATORY_FIELD_MISSING", "CRITICAL", None))


def assess_impact(
    old_lines: list[BomLine],
    new_lines: list[BomLine],
    edges: list[Edge],
    rules: dict[str, Rule],
    artifact_nodes: set[str],
) -> ImpactResult:
    line_diffs = diff(old_lines, new_lines)
    changed = [d for d in line_diffs if d.change_type != "UNCHANGED"]
    seeds = sorted(bom_node(d.line_key) for d in changed)
    fields_by_seed = {bom_node(d.line_key): d.changed_fields or (d.change_type,) for d in changed}
    new_by_key = {line.line_key: line for line in new_lines}
    reaches = traverse_downstream(edges, seeds)
    impacted: list[ImpactedNode] = []
    for r in reaches:
        has_artifact = r.node in artifact_nodes
        if node_type(r.node) == "COMPATIBILITY_CHECK" and not has_artifact:
            reason, sev, evaluation = "MANDATORY_FIELD_MISSING", "CRITICAL", None
        else:
            reason, sev, evaluation = _classify(r.node, rules, new_by_key, has_artifact)
        impacted.append(
            ImpactedNode(
                node=r.node,
                node_type=node_type(r.node),
                severity=sev,
                reason_code=reason,
                path=r.path,
                rule_codes=r.rule_codes,
                changed_fields=fields_by_seed.get(r.path[0], ()),
                required_action=REQUIRED_ACTIONS.get(reason, "Review"),
                artifact_exists=has_artifact or node_type(r.node) == "READINESS_APPROVAL",
                evaluation=evaluation,
            )
        )
    if seeds and not any(i.node == READINESS_NODE for i in impacted):
        # Any BOM change alters the baseline, so release approval is always impacted, even for
        # newly added lines that have no explicit edges yet.
        impacted.append(
            ImpactedNode(
                node=READINESS_NODE,
                node_type="READINESS_APPROVAL",
                severity="CRITICAL",
                reason_code="READINESS_APPROVAL_STALE",
                path=(seeds[0], READINESS_NODE),
                rule_codes=("REL-BASELINE-IMPLICIT",),
                changed_fields=fields_by_seed.get(seeds[0], ()),
                required_action=REQUIRED_ACTIONS["READINESS_APPROVAL_STALE"],
                artifact_exists=True,
                evaluation=None,
            )
        )
    impacted.sort(key=lambda i: (SEVERITY_ORDER[i.severity], i.node))
    all_nodes = {e.source for e in edges} | {e.target for e in edges} | artifact_nodes
    touched = set(seeds) | {i.node for i in impacted}
    unaffected = sorted(n for n in all_nodes if n not in touched and n != READINESS_NODE)
    return ImpactResult(line_diffs, seeds, impacted, unaffected)
