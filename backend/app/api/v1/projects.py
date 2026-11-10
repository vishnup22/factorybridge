import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request, Response
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser
from app.core.config import get_settings
from app.core.errors import NotFound
from app.domain.bom import diff as bom_diff
from app.domain.graph import READINESS_NODE, Edge, node_type
from app.models import (
    ApprovalRequest,
    AuditEvent,
    BomRevision,
    CompatibilityRule,
    Document,
    EngineeringChange,
    ImpactAssessment,
    ManufacturingException,
    Project,
    User,
)
from app.models.enums import ApprovalStatus, ChangeStatus, Role
from app.schemas.api import (
    ApprovalCreate,
    ApprovalDecision,
    ApprovalOut,
    ArtifactCreate,
    ArtifactOut,
    AuditOut,
    BlockerOut,
    BomDiffOut,
    BomItemOut,
    BomRevisionDetail,
    BomRevisionOut,
    ChangeCreate,
    ChangeOut,
    DocumentOut,
    EdgeCreate,
    Envelope,
    ExceptionOut,
    GraphEdgeOut,
    GraphNodeOut,
    GraphOut,
    ImpactOut,
    LineDiffOut,
    ProjectCreate,
    ProjectDetail,
    ProjectSummary,
    PublishRequest,
    ResolveRequest,
    RuleOut,
    TemplateApply,
    WaiveRequest,
)
from app.services import (
    approvals_service,
    artifacts_service,
    audit,
    bom_service,
    changes_service,
    exceptions_service,
    plan_service,
    state,
)
from app.services.access import get_project, require_role
from app.services.idempotency import fingerprint, run_idempotent

router = APIRouter(prefix="/projects", tags=["projects"])

ENG = Role.ENGINEER
APP = Role.APPROVER
ADM = Role.ADMIN
CUS = Role.CUSTOMER
IdemKey = Annotated[str | None, Header(alias="Idempotency-Key")]


def _rev_numbers(db: DB, project: Project) -> dict[uuid.UUID, int]:
    return {
        r.id: r.revision_number
        for r in db.execute(select(BomRevision).where(BomRevision.project_id == project.id)).scalars()
    }


def _summary(db: DB, project: Project) -> ProjectSummary:
    readiness = state.evaluate_readiness(db, project)
    rev = state.current_revision(db, project)
    out = ProjectSummary.model_validate(project)
    out.status = readiness.status
    out.current_revision_number = rev.revision_number if rev else None
    out.open_blocker_count = sum(1 for b in readiness.blockers if b.code != "APPROVAL_REQUIRED")
    return out


def _detail(db: DB, user: User, project: Project) -> ProjectDetail:
    readiness = state.evaluate_readiness(db, project)
    rev = state.current_revision(db, project)
    pending = db.execute(
        select(ApprovalRequest.id).where(
            ApprovalRequest.project_id == project.id, ApprovalRequest.status == ApprovalStatus.PENDING
        )
    ).scalar_one_or_none()
    open_exc = db.execute(
        select(ManufacturingException.severity, func.count())
        .where(
            ManufacturingException.project_id == project.id,
            ManufacturingException.status.in_(exceptions_service.OPEN_STATES),
        )
        .group_by(ManufacturingException.severity)
    ).all()
    docs = db.execute(
        select(Document.processing_status, func.count())
        .where(Document.project_id == project.id)
        .group_by(Document.processing_status)
    ).all()
    customer = user.role == CUS.value
    blockers = [BlockerOut(**b.as_dict()) for b in readiness.blockers]
    if customer:
        # Customer-safe view: no internal sourcing/QC detail, only counts and generic messages.
        blockers = [
            BlockerOut(code=b.code, message="Internal engineering review in progress", ref=None, severity=b.severity)
            for b in blockers
        ]
    s = _summary(db, project)
    return ProjectDetail(
        **s.model_dump(),
        blockers=blockers,
        baseline_hash=None if customer else state.current_baseline_hash(db, project),
        valid_approval_id=uuid.UUID(readiness.valid_approval_id) if readiness.valid_approval_id else None,
        pending_approval_id=pending,
        kpis={
            "bom_revision": f"R{rev.revision_number}" if rev else None,
            "open_exceptions_by_severity": dict(open_exc),
            "critical_open": sum(n for sev, n in open_exc if sev == "CRITICAL"),
            "documents_by_status": dict(docs),
            "approval_fresh": readiness.valid_approval_id is not None,
            "pending_approval": pending is not None,
            "next_action": _next_action(readiness.status, readiness.blockers, pending is not None),
        },
        customer_view=customer,
    )


def _next_action(status: str, blockers: list[Any], pending: bool) -> dict[str, str]:
    codes = {b.code for b in blockers}
    if status == "DRAFT":
        return {"owner": "customer/engineer", "action": "Upload a requirements PDF or BOM CSV"}
    if "EXTRACTION_PENDING" in codes:
        return {"owner": "engineer", "action": "Review document extraction candidates"}
    if "NO_DEPENDENCY_GRAPH" in codes:
        return {"owner": "engineer", "action": "Apply a manufacturing plan (rules, dependencies, artifacts)"}
    if status == "BLOCKED":
        return {"owner": "engineer", "action": "Resolve open exceptions and revalidate artifacts"}
    if status == "REVIEW_REQUIRED":
        return {
            "owner": "approver" if pending else "engineer",
            "action": "Decide pending approval" if pending else "Request approval for current baseline",
        }
    if status == "READY":
        return {"owner": "-", "action": "Baseline released for simulated production"}
    return {"owner": "-", "action": "-"}


# ---------------- projects ----------------
@router.get("", response_model=Envelope[list[ProjectSummary]])
def list_projects(user: CurrentUser, db: DB) -> Envelope[list[ProjectSummary]]:
    rows = db.execute(
        select(Project).where(Project.organization_id == user.organization_id).order_by(Project.created_at.desc())
    ).scalars()
    return Envelope(data=[_summary(db, p) for p in rows])


@router.post("", response_model=Envelope[ProjectDetail], status_code=201)
def create_project(body: ProjectCreate, user: CurrentUser, db: DB) -> Envelope[ProjectDetail]:
    require_role(user, CUS, ENG, ADM)
    p = Project(
        organization_id=user.organization_id,
        name=body.name.strip(),
        sku=body.sku,
        target_quantity=body.target_quantity,
        description=body.description,
        target_delivery_date=body.target_delivery_date,
        created_by=user.id,
    )
    db.add(p)
    db.flush()
    audit.record(
        db,
        organization_id=user.organization_id,
        project_id=p.id,
        actor=user,
        action="PROJECT_CREATED",
        resource_type="project",
        resource_id=p.id,
        after={"name": p.name, "sku": p.sku, "target_quantity": p.target_quantity},
    )
    db.commit()
    return Envelope(data=_detail(db, user, p))


@router.get("/{project_id}", response_model=Envelope[ProjectDetail])
def get_project_detail(project_id: uuid.UUID, user: CurrentUser, db: DB) -> Envelope[ProjectDetail]:
    return Envelope(data=_detail(db, user, get_project(db, user, project_id)))


@router.get("/{project_id}/documents", response_model=Envelope[list[DocumentOut]])
def list_documents(project_id: uuid.UUID, user: CurrentUser, db: DB) -> Envelope[list[DocumentOut]]:
    p = get_project(db, user, project_id)
    rows = db.execute(
        select(Document).where(Document.project_id == p.id).order_by(Document.created_at.desc())
    ).scalars()
    return Envelope(data=[DocumentOut.model_validate(d) for d in rows])


# ---------------- BOM ----------------
@router.get("/{project_id}/bom/revisions", response_model=Envelope[list[BomRevisionOut]])
def list_revisions(project_id: uuid.UUID, user: CurrentUser, db: DB) -> Envelope[list[BomRevisionOut]]:
    p = get_project(db, user, project_id)
    out = []
    for r in bom_service.list_revisions(db, p):
        o = BomRevisionOut.model_validate(r)
        o.is_current = r.id == p.current_bom_revision_id
        out.append(o)
    return Envelope(data=out)


@router.get("/{project_id}/bom/revisions/{rev}", response_model=Envelope[BomRevisionDetail])
def get_revision(project_id: uuid.UUID, rev: int, user: CurrentUser, db: DB) -> Envelope[BomRevisionDetail]:
    p = get_project(db, user, project_id)
    r = bom_service.get_revision(db, p, rev)
    items = [BomItemOut.model_validate(i) for i in bom_service.items(db, r.id)]
    if user.role == CUS.value:
        for i in items:
            i.provenance_json = {}
    o = BomRevisionDetail(**BomRevisionOut.model_validate(r).model_dump(), items=items)
    o.is_current = r.id == p.current_bom_revision_id
    return Envelope(data=o)


@router.get("/{project_id}/bom/diff", response_model=Envelope[BomDiffOut])
def get_diff(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: DB,
    from_rev: int = Query(alias="from"),
    to_rev: int = Query(alias="to"),
) -> Envelope[BomDiffOut]:
    p = get_project(db, user, project_id)
    a, b = bom_service.get_revision(db, p, from_rev), bom_service.get_revision(db, p, to_rev)
    lines = bom_diff(state.load_lines(db, a.id), state.load_lines(db, b.id))
    return Envelope(
        data=BomDiffOut(
            from_revision=from_rev,
            to_revision=to_rev,
            from_hash=a.content_hash,
            to_hash=b.content_hash,
            lines=[LineDiffOut(**d.as_dict()) for d in lines],
        )
    )


# ---------------- changes ----------------
def _change_out(db: DB, c: EngineeringChange, revs: dict[uuid.UUID, int]) -> ChangeOut:
    o = ChangeOut.model_validate(c)
    o.from_revision_number = revs.get(c.from_bom_revision_id)
    o.to_revision_number = revs.get(c.to_bom_revision_id) if c.to_bom_revision_id else None
    author = db.get(User, c.submitted_by)
    o.submitted_by_email = author.email if author else None
    return o


@router.get("/{project_id}/changes", response_model=Envelope[list[ChangeOut]])
def list_changes(project_id: uuid.UUID, user: CurrentUser, db: DB) -> Envelope[list[ChangeOut]]:
    p = get_project(db, user, project_id)
    revs = _rev_numbers(db, p)
    rows = db.execute(
        select(EngineeringChange)
        .where(EngineeringChange.project_id == p.id)
        .order_by(EngineeringChange.created_at.desc())
    ).scalars()
    return Envelope(data=[_change_out(db, c, revs) for c in rows])


@router.post("/{project_id}/changes", response_model=Envelope[ChangeOut], status_code=201)
def create_change(project_id: uuid.UUID, body: ChangeCreate, user: CurrentUser, db: DB) -> Envelope[ChangeOut]:
    require_role(user, ENG, CUS, APP)
    p = get_project(db, user, project_id, lock=True)
    c = changes_service.propose_change(
        db,
        p,
        user,
        base_revision_number=body.base_revision_number,
        reason=body.reason,
        operations=[op.model_dump(exclude_none=True) for op in body.changes],
    )
    db.commit()
    return Envelope(data=_change_out(db, c, _rev_numbers(db, p)))


@router.get("/{project_id}/changes/{change_id}", response_model=Envelope[ChangeOut])
def get_change(project_id: uuid.UUID, change_id: uuid.UUID, user: CurrentUser, db: DB) -> Envelope[ChangeOut]:
    p = get_project(db, user, project_id)
    return Envelope(data=_change_out(db, changes_service.get_change(db, p, change_id), _rev_numbers(db, p)))


@router.post("/{project_id}/changes/{change_id}/withdraw", response_model=Envelope[ChangeOut])
def withdraw_change(project_id: uuid.UUID, change_id: uuid.UUID, user: CurrentUser, db: DB) -> Envelope[ChangeOut]:
    require_role(user, ENG, CUS, APP)
    p = get_project(db, user, project_id, lock=True)
    c = changes_service.get_change(db, p, change_id, lock=True)
    if user.role != ENG.value and c.submitted_by != user.id:
        raise NotFound("Engineering change not found", code="CHANGE_NOT_FOUND")
    changes_service.withdraw(db, p, user, c)
    db.commit()
    return Envelope(data=_change_out(db, c, _rev_numbers(db, p)))


def _impact_out(db: DB, p: Project, c: EngineeringChange, assessment: ImpactAssessment | None) -> ImpactOut:
    revs = _rev_numbers(db, p)
    readiness = state.evaluate_readiness(db, p)
    if assessment is not None:
        g = assessment.impact_graph_json
        return ImpactOut(
            change_id=c.id,
            change_code=c.code,
            from_revision=f"R{revs[c.from_bom_revision_id]}",
            to_revision=f"R{revs[c.to_bom_revision_id]}" if c.to_bom_revision_id else None,
            readiness=readiness.status,
            changed_nodes=g["changed_nodes"],
            impacted=g["impacted"],
            unaffected_nodes=g["unaffected_nodes"],
            line_diffs=g["line_diffs"],
            requires_approval=readiness.status != "READY",
            persisted=True,
            baseline_hash_before=assessment.baseline_hash,
            baseline_hash_after=assessment.new_baseline_hash,
            rule_version=assessment.rule_version,
            computed_at=assessment.computed_at,
        )
    result = changes_service.preview(db, p, c).as_dict()
    return ImpactOut(
        change_id=c.id,
        change_code=c.code,
        from_revision=f"R{revs[c.from_bom_revision_id]}",
        to_revision=None,
        readiness=readiness.status,
        changed_nodes=result["changed_nodes"],
        impacted=result["impacted"],
        unaffected_nodes=result["unaffected_nodes"],
        line_diffs=result["line_diffs"],
        requires_approval=True,
        persisted=False,
        rule_version=get_settings().rule_version,
    )


@router.get("/{project_id}/changes/{change_id}/impact", response_model=Envelope[ImpactOut])
def get_impact(project_id: uuid.UUID, change_id: uuid.UUID, user: CurrentUser, db: DB) -> Envelope[ImpactOut]:
    require_role(user, ENG, APP, ADM)
    p = get_project(db, user, project_id)
    c = changes_service.get_change(db, p, change_id)
    a = db.execute(select(ImpactAssessment).where(ImpactAssessment.change_id == c.id)).scalar_one_or_none()
    if a is None and c.status != ChangeStatus.PROPOSED.value:
        raise NotFound("No impact assessment for this change", code="IMPACT_NOT_FOUND")
    return Envelope(data=_impact_out(db, p, c, a))


@router.post("/{project_id}/changes/{change_id}/publish", response_model=Envelope[ImpactOut])
def publish_change(
    project_id: uuid.UUID,
    change_id: uuid.UUID,
    body: PublishRequest,
    request: Request,
    response: Response,
    user: CurrentUser,
    db: DB,
    idempotency_key: IdemKey = None,
) -> Any:
    require_role(user, ENG)

    def op() -> tuple[int, Any]:
        p = get_project(db, user, project_id, lock=True)
        c = changes_service.get_change(db, p, change_id, lock=True)
        a = changes_service.publish_change(db, p, user, c, expected_baseline_version=body.expected_baseline_version)
        return 200, Envelope(data=_impact_out(db, p, c, a)).model_dump(mode="json")

    status, payload, replayed = run_idempotent(
        db,
        user=user,
        key=idempotency_key,
        request_hash=fingerprint("POST", request.url.path, body.model_dump(mode="json"), user),
        operation=op,
    )
    response.status_code = status
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return payload


# ---------------- graph, rules, artifacts ----------------
@router.get("/{project_id}/dependency-graph", response_model=Envelope[GraphOut])
def dependency_graph(project_id: uuid.UUID, user: CurrentUser, db: DB) -> Envelope[GraphOut]:
    require_role(user, ENG, APP, ADM)
    p = get_project(db, user, project_id)
    edges = state.active_edges(db, p.id)
    arts = state.current_artifacts(db, p.id)
    lines = {line.line_key: line for line in state.load_lines(db, p.current_bom_revision_id)}
    open_by_node: dict[str, list[str]] = {}
    for e in db.execute(
        select(ManufacturingException).where(
            ManufacturingException.project_id == p.id, ManufacturingException.status.in_(exceptions_service.OPEN_STATES)
        )
    ).scalars():
        if e.node_key:
            open_by_node.setdefault(e.node_key, []).append(f"{e.severity}:{e.reason_code}")
    readiness = state.evaluate_readiness(db, p)
    keys = sorted({e.source for e in edges} | {e.target for e in edges} | set(arts))
    nodes = []
    for k in keys:
        t = node_type(k)
        a = arts.get(k)
        if t == "BOM_LINE":
            line = lines.get(k.split(":", 1)[1])
            label, status = (
                (f"{line.line_key} · {line.part_number} rev {line.part_revision}", "CURRENT")
                if line
                else (k, "MISSING")
            )
        elif t == "READINESS_APPROVAL":
            label, status = "Release approval", readiness.status
        else:
            label = a.title if a else k
            status = a.status if a else "MISSING"
        nodes.append(
            GraphNodeOut(
                key=k,
                node_type=t,
                label=label,
                status=status,
                artifact_id=a.id if a else None,
                artifact_revision=a.revision if a else None,
                open_exceptions=sorted(open_by_node.get(k, [])),
            )
        )
    return Envelope(
        data=GraphOut(
            nodes=nodes,
            edges=[
                GraphEdgeOut(source=e.source, target=e.target, dependency_type=e.dependency_type, rule_code=e.rule_code)
                for e in sorted(edges, key=lambda e: (e.source, e.target))
            ],
        )
    )


@router.post("/{project_id}/dependency-edges", status_code=201, response_model=Envelope[GraphEdgeOut])
def add_edge(project_id: uuid.UUID, body: EdgeCreate, user: CurrentUser, db: DB) -> Envelope[GraphEdgeOut]:
    require_role(user, ENG)
    p = get_project(db, user, project_id, lock=True)
    e = Edge(body.source_node_key, body.target_node_key, body.dependency_type, body.rule_code)
    plan_service.add_single_edge(db, p, user, e)
    db.commit()
    return Envelope(
        data=GraphEdgeOut(source=e.source, target=e.target, dependency_type=e.dependency_type, rule_code=e.rule_code)
    )


@router.post("/{project_id}/plan/apply-template", response_model=Envelope[dict[str, Any]])
def apply_template(project_id: uuid.UUID, body: TemplateApply, user: CurrentUser, db: DB) -> Envelope[dict[str, Any]]:
    require_role(user, ENG)
    p = get_project(db, user, project_id, lock=True)
    result = plan_service.apply_template(db, p, user, body.template)
    db.commit()
    return Envelope(data=result)


@router.get("/{project_id}/compatibility-rules", response_model=Envelope[list[RuleOut]])
def list_rules(project_id: uuid.UUID, user: CurrentUser, db: DB) -> Envelope[list[RuleOut]]:
    require_role(user, ENG, APP, ADM)
    p = get_project(db, user, project_id)
    rows = db.execute(
        select(CompatibilityRule).where(CompatibilityRule.project_id == p.id).order_by(CompatibilityRule.rule_code)
    ).scalars()
    return Envelope(data=[RuleOut.model_validate(r) for r in rows])


@router.get("/{project_id}/artifacts", response_model=Envelope[list[ArtifactOut]])
def list_artifacts(
    project_id: uuid.UUID, user: CurrentUser, db: DB, node_key: str | None = None, include_history: bool = False
) -> Envelope[list[ArtifactOut]]:
    require_role(user, ENG, APP, ADM)
    p = get_project(db, user, project_id)
    from app.models import Artifact

    stmt = select(Artifact).where(Artifact.project_id == p.id)
    if node_key:
        stmt = stmt.where(Artifact.node_key == node_key)
    if not include_history:
        stmt = stmt.where(Artifact.is_current.is_(True))
    rows = db.execute(stmt.order_by(Artifact.node_key, Artifact.revision.desc())).scalars()
    return Envelope(data=[ArtifactOut.model_validate(a) for a in rows])


@router.post("/{project_id}/artifacts", status_code=201, response_model=Envelope[ArtifactOut])
def create_artifact(project_id: uuid.UUID, body: ArtifactCreate, user: CurrentUser, db: DB) -> Envelope[ArtifactOut]:
    require_role(user, ENG)
    p = get_project(db, user, project_id, lock=True)
    a = artifacts_service.create_artifact_revision(
        db,
        p,
        user,
        node_key=body.node_key,
        content=body.content,
        title=body.title,
        expected_current_revision=body.expected_current_revision,
    )
    state.recompute_status(db, p, user, "ARTIFACT_REVISED")
    db.commit()
    return Envelope(data=ArtifactOut.model_validate(a))


# ---------------- exceptions ----------------
@router.get("/{project_id}/exceptions", response_model=Envelope[list[ExceptionOut]])
def list_exceptions(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: DB,
    status: str | None = None,
    severity: str | None = None,
    change_id: uuid.UUID | None = None,
    node_key: str | None = None,
) -> Envelope[list[ExceptionOut]]:
    require_role(user, ENG, APP, ADM)
    p = get_project(db, user, project_id)
    stmt = select(ManufacturingException).where(ManufacturingException.project_id == p.id)
    if status:
        stmt = stmt.where(ManufacturingException.status.in_(status.split(",")))
    if severity:
        stmt = stmt.where(ManufacturingException.severity.in_(severity.split(",")))
    if change_id:
        stmt = stmt.where(ManufacturingException.change_id == change_id)
    if node_key:
        stmt = stmt.where(ManufacturingException.node_key == node_key)
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    rows = sorted(
        db.execute(stmt).scalars(),
        key=lambda e: (
            e.status not in exceptions_service.OPEN_STATES,
            order.get(e.severity, 9),
            e.node_key or "",
            e.reason_code,
        ),
    )
    return Envelope(data=[ExceptionOut.model_validate(e) for e in rows])


@router.post("/{project_id}/exceptions/{exception_id}/resolve", response_model=Envelope[ExceptionOut])
def resolve_exception(
    project_id: uuid.UUID, exception_id: uuid.UUID, body: ResolveRequest, user: CurrentUser, db: DB
) -> Envelope[ExceptionOut]:
    require_role(user, ENG)
    p = get_project(db, user, project_id, lock=True)
    exc = exceptions_service.get_exception(db, p, exception_id, lock=True)
    artifacts_service.resolve_exception(
        db,
        p,
        user,
        exc,
        note=body.resolution_note,
        content=body.artifact_content,
        artifact_id=body.artifact_id,
        expected_current_revision=body.expected_current_revision,
    )
    state.recompute_status(db, p, user, "EXCEPTION_RESOLVED")
    db.commit()
    return Envelope(data=ExceptionOut.model_validate(exc))


@router.post("/{project_id}/exceptions/{exception_id}/waive", response_model=Envelope[ExceptionOut])
def waive_exception(
    project_id: uuid.UUID, exception_id: uuid.UUID, body: WaiveRequest, user: CurrentUser, db: DB
) -> Envelope[ExceptionOut]:
    require_role(user, APP)
    p = get_project(db, user, project_id, lock=True)
    exc = exceptions_service.get_exception(db, p, exception_id, lock=True)
    exceptions_service.waive(db, p, exc, user, body.rationale)
    state.recompute_status(db, p, user, "EXCEPTION_WAIVED")
    db.commit()
    return Envelope(data=ExceptionOut.model_validate(exc))


# ---------------- approvals ----------------
def _approval_out(db: DB, p: Project, a: ApprovalRequest, current_hash: str | None = None) -> ApprovalOut:
    o = ApprovalOut.model_validate(a)
    rev = db.get(BomRevision, a.bom_revision_id)
    o.bom_revision_number = rev.revision_number if rev else None
    req, appr = db.get(User, a.requested_by), db.get(User, a.assigned_approver_id)
    o.requested_by_email = req.email if req else None
    o.assigned_approver_email = appr.email if appr else None
    h = current_hash or state.current_baseline_hash(db, p)
    o.is_current_baseline = a.baseline_hash == h and a.baseline_version == p.baseline_version
    return o


@router.get("/{project_id}/approvals", response_model=Envelope[list[ApprovalOut]])
def list_approvals(project_id: uuid.UUID, user: CurrentUser, db: DB) -> Envelope[list[ApprovalOut]]:
    require_role(user, ENG, APP, ADM)
    p = get_project(db, user, project_id)
    h = state.current_baseline_hash(db, p)
    rows = db.execute(
        select(ApprovalRequest).where(ApprovalRequest.project_id == p.id).order_by(ApprovalRequest.created_at.desc())
    ).scalars()
    return Envelope(data=[_approval_out(db, p, a, h) for a in rows])


@router.post("/{project_id}/approvals/request", status_code=201, response_model=Envelope[ApprovalOut])
def request_approval(project_id: uuid.UUID, body: ApprovalCreate, user: CurrentUser, db: DB) -> Envelope[ApprovalOut]:
    require_role(user, ENG)
    p = get_project(db, user, project_id, lock=True)
    a = approvals_service.request_approval(
        db,
        p,
        user,
        expected_baseline_version=body.expected_baseline_version,
        assigned_approver_id=body.assigned_approver_id,
        note=body.note,
    )
    db.commit()
    return Envelope(data=_approval_out(db, p, a))


@router.post("/{project_id}/approvals/{approval_id}/decision", response_model=Envelope[ApprovalOut])
def decide_approval(
    project_id: uuid.UUID,
    approval_id: uuid.UUID,
    body: ApprovalDecision,
    request: Request,
    response: Response,
    user: CurrentUser,
    db: DB,
    idempotency_key: IdemKey = None,
) -> Any:
    require_role(user, APP)

    def op() -> tuple[int, Any]:
        p = get_project(db, user, project_id, lock=True)
        a = approvals_service.get_approval(db, p, approval_id, lock=True)
        approvals_service.decide(
            db,
            p,
            user,
            a,
            decision=body.decision,
            reason=body.reason,
            expected_baseline_hash=body.expected_baseline_hash,
        )
        db.flush()
        return 200, Envelope(data=_approval_out(db, p, a)).model_dump(mode="json")

    status, payload, replayed = run_idempotent(
        db,
        user=user,
        key=idempotency_key,
        request_hash=fingerprint("POST", request.url.path, body.model_dump(mode="json"), user),
        operation=op,
    )
    response.status_code = status
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return payload


# ---------------- audit & readiness ----------------
@router.get("/{project_id}/audit", response_model=Envelope[list[AuditOut]])
def project_audit(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: DB,
    resource_type: str | None = None,
    resource_id: str | None = None,
    action: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> Envelope[list[AuditOut]]:
    require_role(user, ENG, APP, ADM)
    p = get_project(db, user, project_id)
    stmt = select(AuditEvent).where(AuditEvent.project_id == p.id, AuditEvent.organization_id == user.organization_id)
    if resource_type:
        stmt = stmt.where(AuditEvent.resource_type == resource_type)
    if resource_id:
        stmt = stmt.where(AuditEvent.resource_id == resource_id)
    if action:
        stmt = stmt.where(AuditEvent.action.in_(action.split(",")))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(AuditEvent.seq.desc()).offset((page - 1) * page_size).limit(page_size)).scalars()
    return Envelope(
        data=[AuditOut.model_validate(r) for r in rows], meta={"page": page, "page_size": page_size, "total": total}
    )


@router.get("/{project_id}/readiness", response_model=Envelope[dict[str, Any]])
def readiness(project_id: uuid.UUID, user: CurrentUser, db: DB) -> Envelope[dict[str, Any]]:
    p = get_project(db, user, project_id)
    r = state.evaluate_readiness(db, p)
    data = r.as_dict()
    if user.role == CUS.value:
        data["blockers"] = [{"code": b["code"], "severity": b["severity"]} for b in data["blockers"]]
    else:
        data["baseline_hash"] = state.current_baseline_hash(db, p)
        data["baseline_version"] = p.baseline_version
        data["readiness_node"] = READINESS_NODE
    return Envelope(data=data)
