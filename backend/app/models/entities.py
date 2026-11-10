import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin, enum_check, utcnow
from app.models.enums import (
    ApprovalStatus,
    ArtifactStatus,
    ArtifactType,
    BomRevisionStatus,
    CandidateKind,
    ChangeStatus,
    Confidence,
    DocumentStatus,
    EvidenceStatus,
    ExceptionStatus,
    ExtractionStatus,
    JobStatus,
    ProjectStatus,
    ProviderMode,
    ReviewState,
    Role,
    Severity,
    values,
)

JSON = JSONB


def _fk(target: str, nullable: bool = False, ondelete: str | None = None) -> Mapped[Any]:
    return mapped_column(Uuid, ForeignKey(target, ondelete=ondelete), nullable=nullable, index=True)


class Organization(IdMixin, TimestampMixin, Base):
    __tablename__ = "organizations"
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("organization_id", "email", name="uq_users_org_email"),
        CheckConstraint(enum_check("role", values(Role)), name="role"),
    )
    organization_id: Mapped[uuid.UUID] = _fk("organizations.id")
    email: Mapped[str] = mapped_column(String(254))
    full_name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Project(IdMixin, TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("target_quantity > 0", name="target_quantity_positive"),
        CheckConstraint(enum_check("status", values(ProjectStatus)), name="status"),
        Index("ix_projects_org_created", "organization_id", "created_at"),
    )
    organization_id: Mapped[uuid.UUID] = _fk("organizations.id")
    name: Mapped[str] = mapped_column(String(200))
    sku: Mapped[str] = mapped_column(String(80))
    target_quantity: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text, default="")
    target_delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=ProjectStatus.DRAFT.value)
    current_bom_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("bom_revisions.id", use_alter=True, name="fk_projects_current_bom"), nullable=True
    )
    baseline_version: Mapped[int] = mapped_column(Integer, default=0)
    row_version: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[uuid.UUID] = _fk("users.id")


class Document(IdMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("project_id", "sha256", name="uq_documents_project_sha"),
        CheckConstraint(enum_check("processing_status", values(DocumentStatus)), name="processing_status"),
        Index("ix_documents_project_created", "project_id", "created_at"),
    )
    organization_id: Mapped[uuid.UUID] = _fk("organizations.id")
    project_id: Mapped[uuid.UUID] = _fk("projects.id")
    filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(300))
    processing_status: Mapped[str] = mapped_column(String(32), default=DocumentStatus.PENDING.value)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uploaded_by: Mapped[uuid.UUID] = _fk("users.id")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExtractionRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "extraction_runs"
    __table_args__ = (
        CheckConstraint(enum_check("status", values(ExtractionStatus)), name="status"),
        CheckConstraint(enum_check("provider_mode", values(ProviderMode)), name="provider_mode"),
    )
    organization_id: Mapped[uuid.UUID] = _fk("organizations.id")
    project_id: Mapped[uuid.UUID] = _fk("projects.id")
    document_id: Mapped[uuid.UUID] = _fk("documents.id")
    dedup_key: Mapped[str] = mapped_column(String(200), unique=True)
    provider_mode: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(120))
    prompt_version: Mapped[str] = mapped_column(String(40))
    schema_version: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(32), default=ExtractionStatus.PENDING.value)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd_estimate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    cost_assumptions: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    raw_output_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resulting_bom_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("bom_revisions.id"), nullable=True
    )
    resulting_change_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("engineering_changes.id"), nullable=True
    )


class RequirementCandidate(IdMixin, TimestampMixin, Base):
    __tablename__ = "requirement_candidates"
    __table_args__ = (
        UniqueConstraint("extraction_run_id", "field_path", name="uq_candidates_run_field"),
        CheckConstraint(enum_check("kind", values(CandidateKind)), name="kind"),
        CheckConstraint(enum_check("review_state", values(ReviewState)), name="review_state"),
        CheckConstraint(enum_check("evidence_status", values(EvidenceStatus)), name="evidence_status"),
        CheckConstraint(enum_check("confidence_label", values(Confidence)), name="confidence_label"),
    )
    extraction_run_id: Mapped[uuid.UUID] = _fk("extraction_runs.id")
    kind: Mapped[str] = mapped_column(String(20))
    field_path: Mapped[str] = mapped_column(String(200))
    value_json: Mapped[Any] = mapped_column(JSON)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_section: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_status: Mapped[str] = mapped_column(String(20))
    confidence_label: Mapped[str] = mapped_column(String(20))
    review_state: Mapped[str] = mapped_column(String(20), default=ReviewState.PENDING.value)
    issues_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    corrected_value_json: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    correction_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BomRevision(IdMixin, TimestampMixin, Base):
    __tablename__ = "bom_revisions"
    __table_args__ = (
        UniqueConstraint("project_id", "revision_number", name="uq_bom_revisions_project_rev"),
        CheckConstraint(enum_check("status", values(BomRevisionStatus)), name="status"),
        CheckConstraint("revision_number > 0", name="revision_positive"),
    )
    organization_id: Mapped[uuid.UUID] = _fk("organizations.id")
    project_id: Mapped[uuid.UUID] = _fk("projects.id")
    revision_number: Mapped[int] = mapped_column(Integer)
    parent_revision_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("bom_revisions.id"), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default=BomRevisionStatus.PUBLISHED.value)
    source_kind: Mapped[str] = mapped_column(String(32))
    source_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_by: Mapped[uuid.UUID] = _fk("users.id")


class BomItem(IdMixin, Base):
    __tablename__ = "bom_items"
    __table_args__ = (
        UniqueConstraint("bom_revision_id", "line_key", name="uq_bom_items_rev_line"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
    )
    bom_revision_id: Mapped[uuid.UUID] = _fk("bom_revisions.id")
    line_key: Mapped[str] = mapped_column(String(80))
    part_number: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(300), default="")
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    unit: Mapped[str] = mapped_column(String(20))
    part_revision: Mapped[str] = mapped_column(String(20))
    attributes_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    provenance_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SupplierOption(IdMixin, TimestampMixin, Base):
    __tablename__ = "supplier_options"
    __table_args__ = (
        CheckConstraint("synthetic = true", name="synthetic_only"),
        UniqueConstraint("organization_id", "vendor_name", "part_number", name="uq_supplier_vendor_part"),
        UniqueConstraint("organization_id", "option_code", name="uq_supplier_option_code"),
    )
    organization_id: Mapped[uuid.UUID] = _fk("organizations.id")
    option_code: Mapped[str] = mapped_column(String(40))
    part_number: Mapped[str] = mapped_column(String(80), index=True)
    part_revision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    vendor_name: Mapped[str] = mapped_column(String(200))
    available_qty: Mapped[int] = mapped_column(Integer)
    lead_days: Mapped[int] = mapped_column(Integer)
    unit_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    approved_for_part: Mapped[bool] = mapped_column(Boolean, default=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True)


class CompatibilityRule(IdMixin, TimestampMixin, Base):
    __tablename__ = "compatibility_rules"
    __table_args__ = (UniqueConstraint("project_id", "rule_code", name="uq_rules_project_code"),)
    organization_id: Mapped[uuid.UUID] = _fk("organizations.id")
    project_id: Mapped[uuid.UUID] = _fk("projects.id")
    rule_code: Mapped[str] = mapped_column(String(80))
    check_kind: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text, default="")
    params_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    is_critical: Mapped[bool] = mapped_column(Boolean, default=True)


class Artifact(IdMixin, TimestampMixin, Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("project_id", "node_key", "revision", name="uq_artifacts_node_rev"),
        CheckConstraint(enum_check("artifact_type", values(ArtifactType)), name="artifact_type"),
        CheckConstraint(enum_check("status", values(ArtifactStatus)), name="status"),
        Index(
            "uq_artifacts_one_current",
            "project_id",
            "node_key",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )
    organization_id: Mapped[uuid.UUID] = _fk("organizations.id")
    project_id: Mapped[uuid.UUID] = _fk("projects.id")
    node_key: Mapped[str] = mapped_column(String(160))
    artifact_type: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(32))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    based_on_hash: Mapped[str] = mapped_column(String(64))
    validation_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[uuid.UUID] = _fk("users.id")


class DependencyEdge(IdMixin, TimestampMixin, Base):
    __tablename__ = "dependency_edges"
    __table_args__ = (
        UniqueConstraint("project_id", "source_node_key", "target_node_key", "dependency_type", name="uq_edges_scoped"),
        CheckConstraint("source_node_key <> target_node_key", name="no_self_loop"),
    )
    organization_id: Mapped[uuid.UUID] = _fk("organizations.id")
    project_id: Mapped[uuid.UUID] = _fk("projects.id")
    source_node_key: Mapped[str] = mapped_column(String(160))
    target_node_key: Mapped[str] = mapped_column(String(160))
    dependency_type: Mapped[str] = mapped_column(String(60))
    rule_code: Mapped[str] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class EngineeringChange(IdMixin, TimestampMixin, Base):
    __tablename__ = "engineering_changes"
    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_changes_project_code"),
        CheckConstraint(enum_check("status", values(ChangeStatus)), name="status"),
        Index("ix_changes_project_status", "project_id", "status"),
    )
    organization_id: Mapped[uuid.UUID] = _fk("organizations.id")
    project_id: Mapped[uuid.UUID] = _fk("projects.id")
    code: Mapped[str] = mapped_column(String(20))
    from_bom_revision_id: Mapped[uuid.UUID] = _fk("bom_revisions.id")
    to_bom_revision_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("bom_revisions.id"), nullable=True)
    submitted_by: Mapped[uuid.UUID] = _fk("users.id")
    published_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=ChangeStatus.PROPOSED.value)
    change_set_json: Mapped[list[Any]] = mapped_column(JSON)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ImpactAssessment(IdMixin, Base):
    __tablename__ = "impact_assessments"
    change_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("engineering_changes.id"), unique=True, index=True)
    project_id: Mapped[uuid.UUID] = _fk("projects.id")
    baseline_hash: Mapped[str] = mapped_column(String(64))
    new_baseline_hash: Mapped[str] = mapped_column(String(64))
    impact_graph_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    rule_version: Mapped[str] = mapped_column(String(40))
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ManufacturingException(IdMixin, TimestampMixin, Base):
    __tablename__ = "exceptions"
    __table_args__ = (
        CheckConstraint(enum_check("status", values(ExceptionStatus)), name="status"),
        CheckConstraint(enum_check("severity", values(Severity)), name="severity"),
        Index("ix_exceptions_project_status", "project_id", "status"),
    )
    organization_id: Mapped[uuid.UUID] = _fk("organizations.id")
    project_id: Mapped[uuid.UUID] = _fk("projects.id")
    change_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("engineering_changes.id"), nullable=True, index=True
    )
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("artifacts.id"), nullable=True)
    extraction_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("extraction_runs.id"), nullable=True)
    node_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    dedup_key: Mapped[str] = mapped_column(String(300), unique=True)
    reason_code: Mapped[str] = mapped_column(String(60))
    severity: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default=ExceptionStatus.OPEN.value)
    description: Mapped[str] = mapped_column(Text)
    required_action: Mapped[str] = mapped_column(Text)
    owner_role: Mapped[str] = mapped_column(String(20), default=Role.ENGINEER.value)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reopened_count: Mapped[int] = mapped_column(Integer, default=0)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_artifact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("artifacts.id"), nullable=True)


class ApprovalRequest(IdMixin, TimestampMixin, Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint(enum_check("status", values(ApprovalStatus)), name="status"),
        Index(
            "uq_approval_one_pending",
            "project_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index("ix_approvals_project_created", "project_id", "created_at"),
    )
    organization_id: Mapped[uuid.UUID] = _fk("organizations.id")
    project_id: Mapped[uuid.UUID] = _fk("projects.id")
    bom_revision_id: Mapped[uuid.UUID] = _fk("bom_revisions.id")
    baseline_hash: Mapped[str] = mapped_column(String(64))
    baseline_version: Mapped[int] = mapped_column(Integer)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    requested_by: Mapped[uuid.UUID] = _fk("users.id")
    assigned_approver_id: Mapped[uuid.UUID] = _fk("users.id")
    status: Mapped[str] = mapped_column(String(20), default=ApprovalStatus.PENDING.value)
    reason: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(10), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    stale_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    staled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(IdMixin, Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_project_created", "project_id", "created_at"),)
    # Commit-order-independent total order of audit writes; timestamps alone can tie.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True, nullable=False)
    organization_id: Mapped[uuid.UUID] = _fk("organizations.id")
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("projects.id"), nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    actor_label: Mapped[str] = mapped_column(String(120), default="")
    action: Mapped[str] = mapped_column(String(80))
    resource_type: Mapped[str] = mapped_column(String(60))
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ProcessingJob(IdMixin, TimestampMixin, Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (CheckConstraint(enum_check("status", values(JobStatus)), name="status"),)
    organization_id: Mapped[uuid.UUID] = _fk("organizations.id")
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("projects.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(60))
    resource_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    dedup_key: Mapped[str] = mapped_column(String(200), unique=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.QUEUED.value)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OutboxEvent(IdMixin, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_unpublished", "created_at", postgresql_where=text("published_at IS NULL")),)
    organization_id: Mapped[uuid.UUID] = _fk("organizations.id")
    event_type: Mapped[str] = mapped_column(String(80))
    aggregate_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class IdempotencyRecord(IdMixin, Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("organization_id", "request_key", name="uq_idem_org_key"),)
    organization_id: Mapped[uuid.UUID] = _fk("organizations.id")
    user_id: Mapped[uuid.UUID] = _fk("users.id")
    request_key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    response_json: Mapped[Any] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
