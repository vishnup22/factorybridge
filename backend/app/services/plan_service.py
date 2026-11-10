"""Manufacturing plan: explicit compatibility rules, dependency edges, and plan templates."""

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.domain.graph import READINESS_NODE, Edge, bom_node, find_cycle, validate_node_key
from app.domain.rules import Rule, validate_rule_definition
from app.models import CompatibilityRule, DependencyEdge, Project, User
from app.services import artifacts_service, audit, state

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "demo" / "fixtures"
TEMPLATES = {"warehouse_scout_v1": ("compatibility_rules.json", "dependency_plan.json")}


def _assert_acyclic(existing: list[Edge], new: list[Edge]) -> None:
    cycle = find_cycle(existing + new)
    if cycle:
        raise ValidationFailed("Dependency edge would create a cycle", code="CYCLE_DETECTED", extra={"cycle": cycle})


def add_edges(db: Session, project: Project, actor: User, edges: list[Edge], *, audit_each: bool = True) -> int:
    existing = state.active_edges(db, project.id)
    have = {(e.source, e.target, e.dependency_type) for e in existing}
    fresh: list[Edge] = []
    for e in edges:
        for n in (e.source, e.target):
            if not validate_node_key(n):
                raise ValidationFailed(f"Invalid node key {n!r}", code="INVALID_NODE_KEY")
        if e.source == e.target:
            raise ValidationFailed(
                "Self-loop edges are not allowed", code="CYCLE_DETECTED", extra={"cycle": [e.source]}
            )
        if (e.source, e.target, e.dependency_type) not in have:
            fresh.append(e)
            have.add((e.source, e.target, e.dependency_type))
    if not fresh:
        return 0
    _assert_acyclic(existing, fresh)
    for e in fresh:
        db.add(
            DependencyEdge(
                organization_id=project.organization_id,
                project_id=project.id,
                source_node_key=e.source,
                target_node_key=e.target,
                dependency_type=e.dependency_type,
                rule_code=e.rule_code,
            )
        )
    db.flush()
    if audit_each:
        audit.record(
            db,
            organization_id=project.organization_id,
            project_id=project.id,
            actor=actor,
            action="DEPENDENCY_EDGES_ADDED",
            resource_type="dependency_graph",
            resource_id=project.id,
            after={"edges": [[e.source, e.target, e.dependency_type, e.rule_code] for e in fresh]},
        )
    return len(fresh)


def upsert_rules(db: Session, project: Project, actor: User, rules: list[dict[str, Any]]) -> list[Edge]:
    """Insert rules (identical re-insert is a no-op; differing definition is a conflict). Returns the
    BOM_LINE -> COMPATIBILITY_CHECK edges implied by each rule's referenced lines."""
    edges: list[Edge] = []
    for raw in rules:
        rule = Rule(
            raw["rule_code"],
            raw["check_kind"],
            raw["params"],
            bool(raw.get("is_critical", True)),
            raw.get("description", ""),
        )
        errs = validate_rule_definition(rule)
        if errs:
            raise ValidationFailed(f"Invalid rule {rule.rule_code}", code="INVALID_RULE", extra={"errors": errs})
        existing = db.execute(
            select(CompatibilityRule).where(
                CompatibilityRule.project_id == project.id, CompatibilityRule.rule_code == rule.rule_code
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                CompatibilityRule(
                    organization_id=project.organization_id,
                    project_id=project.id,
                    rule_code=rule.rule_code,
                    check_kind=rule.check_kind,
                    description=rule.description,
                    params_json=rule.params,
                    is_critical=rule.is_critical,
                )
            )
            audit.record(
                db,
                organization_id=project.organization_id,
                project_id=project.id,
                actor=actor,
                action="COMPATIBILITY_RULE_ADDED",
                resource_type="compatibility_rule",
                resource_id=None,
                after={"rule_code": rule.rule_code, "check_kind": rule.check_kind, "params": rule.params},
            )
        elif existing.params_json != rule.params or existing.check_kind != rule.check_kind:
            raise Conflict(f"Rule {rule.rule_code} already exists with a different definition", code="RULE_CONFLICT")
        for line_key in rule.line_keys():
            edges.append(
                Edge(bom_node(line_key), f"COMPATIBILITY_CHECK:{rule.rule_code}", "COMPATIBILITY_INPUT", rule.rule_code)
            )
    db.flush()
    return edges


def load_template(name: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if name not in TEMPLATES:
        raise NotFound(f"Unknown plan template {name}", code="TEMPLATE_NOT_FOUND")
    rules_file, plan_file = TEMPLATES[name]
    rules = json.loads((FIXTURES_DIR / rules_file).read_text(encoding="utf-8"))["rules"]
    plan = json.loads((FIXTURES_DIR / plan_file).read_text(encoding="utf-8"))
    return rules, plan


def apply_template(db: Session, project: Project, actor: User, name: str) -> dict[str, Any]:
    """Engineer action: register the template's explicit rules + edges, then create initial
    artifacts. Every artifact still goes through deterministic validation; a template that does not
    match the current BOM yields INVALID artifacts, not silently valid ones."""
    if project.current_bom_revision_id is None:
        raise ValidationFailed("Publish BOM R1 before applying a manufacturing plan", code="NO_BOM_REVISION")
    rules, plan = load_template(name)
    rule_edges = upsert_rules(db, project, actor, rules)
    lines = state.load_lines(db, project.current_bom_revision_id)
    explicit = [Edge(e["source"], e["target"], e["dependency_type"], e["rule_code"]) for e in plan["edges"]]
    release = [Edge(bom_node(line.line_key), READINESS_NODE, "RELEASE_BASELINE", "REL-BASELINE") for line in lines]
    added = add_edges(db, project, actor, rule_edges + explicit + release)
    existing = state.current_artifacts(db, project.id)
    created: list[dict[str, Any]] = []
    for spec in plan["artifacts"]:
        if spec["node_key"] in existing:
            continue
        art = artifacts_service.create_artifact_revision(
            db,
            project,
            actor,
            node_key=spec["node_key"],
            content=dict(spec.get("content", {})),
            title=spec.get("title", ""),
            cause="PLAN_TEMPLATE_APPLIED",
        )
        created.append({"node_key": art.node_key, "revision": art.revision, "status": art.status})
    if added and not created:
        state.invalidate_baseline(db, project, actor, f"PLAN_TEMPLATE_APPLIED:{name}")
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        actor=actor,
        action="PLAN_TEMPLATE_APPLIED",
        resource_type="project",
        resource_id=project.id,
        after={"template": name, "edges_added": added, "artifacts_created": created},
    )
    state.recompute_status(db, project, actor, f"PLAN_TEMPLATE_APPLIED:{name}")
    return {"template": name, "edges_added": added, "artifacts_created": created}


def add_single_edge(db: Session, project: Project, actor: User, edge: Edge) -> None:
    if add_edges(db, project, actor, [edge]) == 0:
        raise Conflict("Edge already exists", code="EDGE_EXISTS")
    state.invalidate_baseline(db, project, actor, f"DEPENDENCY_EDGE_ADDED:{edge.source}->{edge.target}")
    state.recompute_status(db, project, actor, "DEPENDENCY_EDGE_ADDED")
