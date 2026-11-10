"""Public API contract (request/response models). Changes here are API contract changes; see
API_CONTRACTS.md for ownership rules."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Envelope[T](BaseModel):
    data: T
    meta: dict[str, Any] = Field(default_factory=dict)


class Problem(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str
    code: str
    correlation_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- auth ----------
class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=1, max_length=200)
    organization_slug: str | None = None


class UserOut(ORM):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    organization_id: uuid.UUID
    is_active: bool


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserOut


class MeResponse(BaseModel):
    user: UserOut
    organization: dict[str, Any]
    ai_mode: str
    environment: str


# ---------- projects ----------
class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    sku: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9\-_.]+$")
    target_quantity: int = Field(gt=0, le=1_000_000)
    description: str = Field(default="", max_length=4000)
    target_delivery_date: date | None = None


class BlockerOut(BaseModel):
    code: str
    message: str
    ref: str | None
    severity: str


class ProjectOut(ORM):
    id: uuid.UUID
    name: str
    sku: str
    target_quantity: int
    description: str
    target_delivery_date: date | None
    status: str
    current_bom_revision_id: uuid.UUID | None
    baseline_version: int
    row_version: int
    created_at: datetime
    updated_at: datetime


class ProjectSummary(ProjectOut):
    current_revision_number: int | None = None
    open_blocker_count: int = 0


class ProjectDetail(ProjectSummary):
    blockers: list[BlockerOut]
    baseline_hash: str | None
    valid_approval_id: uuid.UUID | None
    pending_approval_id: uuid.UUID | None
    kpis: dict[str, Any]
    customer_view: bool = False


# ---------- documents ----------
class JobOut(ORM):
    id: uuid.UUID
    kind: str
    resource_id: uuid.UUID
    attempt: int
    status: str
    error_code: str | None
    error_detail: str | None
    started_at: datetime | None
    finished_at: datetime | None
    next_retry_at: datetime | None
    created_at: datetime


class DocumentOut(ORM):
    id: uuid.UUID
    project_id: uuid.UUID
    filename: str
    media_type: str
    sha256: str
    size_bytes: int
    processing_status: str
    page_count: int | None
    error_code: str | None
    error_detail: str | None
    created_at: datetime


class DocumentDetail(DocumentOut):
    job: JobOut | None
    download_url: str | None
    duplicate: bool = False


class CandidateOut(ORM):
    id: uuid.UUID
    kind: str
    field_path: str
    value_json: Any
    unit: str | None
    source_page: int | None
    source_section: str | None
    source_excerpt: str | None
    evidence_status: str
    confidence_label: str
    review_state: str
    issues_json: list[Any]
    corrected_value_json: Any | None
    correction_note: str | None


class ExtractionRunOut(ORM):
    id: uuid.UUID
    document_id: uuid.UUID
    provider_mode: str
    model: str
    prompt_version: str
    schema_version: str
    status: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    cost_usd_estimate: float | None
    cost_assumptions: dict[str, Any] | None
    error_code: str | None
    error_detail: str | None
    summary_json: dict[str, Any]
    resulting_bom_revision_id: uuid.UUID | None
    resulting_change_id: uuid.UUID | None
    created_at: datetime


class ExtractionOut(BaseModel):
    document: DocumentOut
    run: ExtractionRunOut | None
    candidates: list[CandidateOut]


class CandidateDecision(BaseModel):
    candidate_id: uuid.UUID
    action: Literal["ACCEPT", "EDIT", "REJECT"]
    value: Any | None = None
    note: str | None = Field(default=None, max_length=2000)


class AcceptRequest(BaseModel):
    decisions: list[CandidateDecision] = Field(max_length=1000)
    reason: str = Field(min_length=3, max_length=2000)


class AcceptResponse(BaseModel):
    bom_revision: int | None
    change_id: uuid.UUID | None
    change_code: str | None
    project_status: str


# ---------- BOM ----------
class BomItemOut(ORM):
    line_key: str
    part_number: str
    description: str
    quantity: float
    unit: str
    part_revision: str
    attributes_json: dict[str, Any]
    provenance_json: dict[str, Any]


class BomRevisionOut(ORM):
    id: uuid.UUID
    revision_number: int
    parent_revision_id: uuid.UUID | None
    content_hash: str
    status: str
    source_kind: str
    source_ref: uuid.UUID | None
    created_by: uuid.UUID
    created_at: datetime
    is_current: bool = False


class BomRevisionDetail(BomRevisionOut):
    items: list[BomItemOut]


class LineDiffOut(BaseModel):
    line_key: str
    change_type: str
    old: dict[str, Any] | None
    new: dict[str, Any] | None
    changed_fields: list[str]
    reason_codes: list[str]


class BomDiffOut(BaseModel):
    from_revision: int
    to_revision: int
    from_hash: str
    to_hash: str
    lines: list[LineDiffOut]


# ---------- changes / impact ----------
class ChangeOperation(BaseModel):
    line_key: str = Field(min_length=1, max_length=80)
    operation: Literal["UPDATE", "ADD", "REMOVE"]
    new: dict[str, Any] | None = None


class ChangeCreate(BaseModel):
    base_revision_number: int = Field(ge=1)
    reason: str = Field(min_length=5, max_length=4000)
    changes: list[ChangeOperation] = Field(min_length=1, max_length=200)


class PublishRequest(BaseModel):
    expected_baseline_version: int | None = None


class ImpactedNodeOut(BaseModel):
    node: str
    node_type: str
    severity: str
    reason_code: str
    path: list[str]
    rule_codes: list[str]
    changed_fields: list[str]
    required_action: str
    artifact_exists: bool
    evaluation: dict[str, Any] | None


class ImpactOut(BaseModel):
    change_id: uuid.UUID
    change_code: str
    from_revision: str
    to_revision: str | None
    readiness: str
    changed_nodes: list[str]
    impacted: list[ImpactedNodeOut]
    unaffected_nodes: list[str]
    line_diffs: list[LineDiffOut]
    requires_approval: bool
    persisted: bool
    baseline_hash_before: str | None = None
    baseline_hash_after: str | None = None
    rule_version: str
    computed_at: datetime | None = None


class ChangeOut(ORM):
    id: uuid.UUID
    code: str
    from_bom_revision_id: uuid.UUID
    to_bom_revision_id: uuid.UUID | None
    submitted_by: uuid.UUID
    published_by: uuid.UUID | None
    reason: str
    status: str
    change_set_json: list[Any]
    published_at: datetime | None
    created_at: datetime
    from_revision_number: int | None = None
    to_revision_number: int | None = None
    submitted_by_email: str | None = None


# ---------- graph / artifacts ----------
class GraphNodeOut(BaseModel):
    key: str
    node_type: str
    label: str
    status: str
    artifact_id: uuid.UUID | None = None
    artifact_revision: int | None = None
    open_exceptions: list[str] = Field(default_factory=list)


class GraphEdgeOut(BaseModel):
    source: str
    target: str
    dependency_type: str
    rule_code: str


class GraphOut(BaseModel):
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]


class EdgeCreate(BaseModel):
    source_node_key: str = Field(max_length=160)
    target_node_key: str = Field(max_length=160)
    dependency_type: str = Field(min_length=2, max_length=60)
    rule_code: str = Field(min_length=2, max_length=80)


class ArtifactOut(ORM):
    id: uuid.UUID
    node_key: str
    artifact_type: str
    revision: int
    title: str
    status: str
    is_current: bool
    content_json: dict[str, Any]
    content_hash: str
    based_on_hash: str
    validation_json: dict[str, Any]
    created_by: uuid.UUID
    created_at: datetime


class ArtifactCreate(BaseModel):
    node_key: str = Field(max_length=160)
    title: str = Field(default="", max_length=200)
    content: dict[str, Any] = Field(default_factory=dict)
    expected_current_revision: int | None = None


class TemplateApply(BaseModel):
    template: Literal["warehouse_scout_v1"] = "warehouse_scout_v1"


class SupplierOut(ORM):
    id: uuid.UUID
    option_code: str
    vendor_name: str
    part_number: str
    part_revision: str | None
    available_qty: int
    lead_days: int
    unit_cost_usd: float | None
    approved_for_part: bool
    synthetic: bool


class RuleOut(ORM):
    rule_code: str
    check_kind: str
    description: str
    params_json: dict[str, Any]
    is_critical: bool


# ---------- exceptions ----------
class ExceptionOut(ORM):
    id: uuid.UUID
    change_id: uuid.UUID | None
    artifact_id: uuid.UUID | None
    extraction_run_id: uuid.UUID | None
    node_key: str | None
    reason_code: str
    severity: str
    status: str
    description: str
    required_action: str
    owner_role: str
    evidence_json: dict[str, Any]
    reopened_count: int
    resolved_by: uuid.UUID | None
    resolved_at: datetime | None
    resolution_note: str | None
    resolution_artifact_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ResolveRequest(BaseModel):
    resolution_note: str = Field(min_length=5, max_length=4000)
    artifact_content: dict[str, Any] | None = None
    artifact_id: uuid.UUID | None = None
    expected_current_revision: int | None = None


class WaiveRequest(BaseModel):
    rationale: str = Field(min_length=10, max_length=4000)


# ---------- approvals ----------
class ApprovalCreate(BaseModel):
    expected_baseline_version: int
    assigned_approver_id: uuid.UUID | None = None
    note: str = Field(default="", max_length=4000)


class ApprovalDecision(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    reason: str = Field(min_length=3, max_length=4000)
    expected_baseline_hash: str | None = Field(default=None, min_length=64, max_length=64)


class ApprovalOut(ORM):
    id: uuid.UUID
    bom_revision_id: uuid.UUID
    baseline_hash: str
    baseline_version: int
    requested_by: uuid.UUID
    assigned_approver_id: uuid.UUID
    status: str
    decision: str | None
    reason: str
    decided_by: uuid.UUID | None
    reviewed_at: datetime | None
    decision_reason: str | None
    stale_reason: str | None
    staled_at: datetime | None
    created_at: datetime
    bom_revision_number: int | None = None
    requested_by_email: str | None = None
    assigned_approver_email: str | None = None
    is_current_baseline: bool = False


# ---------- audit ----------
class AuditOut(ORM):
    id: uuid.UUID
    seq: int
    project_id: uuid.UUID | None
    actor_id: uuid.UUID | None
    actor_label: str
    action: str
    resource_type: str
    resource_id: str | None
    before_json: dict[str, Any] | None
    after_json: dict[str, Any] | None
    correlation_id: str | None
    created_at: datetime


# ---------- admin ----------
class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    full_name: str = Field(default="", max_length=200)
    role: Literal["customer", "engineer", "approver", "admin"]
    password: str = Field(min_length=12, max_length=200)


class UserUpdate(BaseModel):
    role: Literal["customer", "engineer", "approver", "admin"] | None = None
    is_active: bool | None = None
