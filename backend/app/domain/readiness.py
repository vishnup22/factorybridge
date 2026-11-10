"""Readiness state derivation (Invariant I-1). Pure function of persisted facts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

BLOCKING_SEVERITIES = frozenset({"CRITICAL", "HIGH"})


@dataclass(frozen=True)
class Blocker:
    code: str
    message: str
    ref: str | None = None
    severity: str = "CRITICAL"

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "ref": self.ref, "severity": self.severity}


@dataclass
class ReadinessFacts:
    has_bom: bool
    pending_extractions: int
    graph_node_count: int
    required_nodes: list[str]
    artifact_status: dict[str, str]  # node_key -> current artifact status
    open_exceptions: list[tuple[str, str, str]]  # (id, severity, reason_code)
    current_baseline_hash: str
    current_baseline_version: int
    approved: list[tuple[str, str, int]] = field(default_factory=list)  # (id, hash, version) APPROVED


@dataclass(frozen=True)
class Readiness:
    status: str
    blockers: list[Blocker]
    valid_approval_id: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "blockers": [b.as_dict() for b in self.blockers],
            "valid_approval_id": self.valid_approval_id,
        }


def compute_blockers(f: ReadinessFacts) -> list[Blocker]:
    blockers: list[Blocker] = []
    if not f.has_bom:
        blockers.append(Blocker("NO_BOM_REVISION", "No published BOM revision"))
    if f.pending_extractions:
        blockers.append(
            Blocker("EXTRACTION_PENDING", f"{f.pending_extractions} document(s) still processing or unreviewed")
        )
    if f.has_bom and f.graph_node_count == 0:
        blockers.append(Blocker("NO_DEPENDENCY_GRAPH", "No manufacturing dependency graph is defined"))
    for node in sorted(f.required_nodes):
        status = f.artifact_status.get(node)
        if status is None:
            blockers.append(Blocker("ARTIFACT_MISSING", f"{node} has no artifact record", node))
        elif status != "VALID":
            blockers.append(Blocker("ARTIFACT_NOT_VALID", f"{node} is {status}", node))
    for exc_id, severity, reason in sorted(f.open_exceptions, key=lambda x: (x[1], x[0])):
        if severity in BLOCKING_SEVERITIES:
            blockers.append(Blocker("OPEN_EXCEPTION", f"{severity} {reason} unresolved", exc_id, severity))
    return blockers


def valid_approval(f: ReadinessFacts) -> str | None:
    for approval_id, h, v in f.approved:
        if h == f.current_baseline_hash and v == f.current_baseline_version:
            return approval_id
    return None


def compute_readiness(f: ReadinessFacts) -> Readiness:
    blockers = compute_blockers(f)
    if not f.has_bom:
        status = "EXTRACTING" if f.pending_extractions else "DRAFT"
        return Readiness(status, blockers, None)
    if blockers:
        only_extraction = all(b.code == "EXTRACTION_PENDING" for b in blockers)
        return Readiness("EXTRACTING" if only_extraction else "BLOCKED", blockers, None)
    approval = valid_approval(f)
    if approval is None:
        return Readiness(
            "REVIEW_REQUIRED",
            [Blocker("APPROVAL_REQUIRED", "Current baseline has no valid approval", severity="HIGH")],
            None,
        )
    return Readiness("READY", [], approval)
