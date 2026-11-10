"""Constrained string enums. Values are stored as text with CHECK constraints."""

from enum import StrEnum


class Role(StrEnum):
    CUSTOMER = "customer"
    ENGINEER = "engineer"
    APPROVER = "approver"
    ADMIN = "admin"


class ProjectStatus(StrEnum):
    DRAFT = "DRAFT"
    EXTRACTING = "EXTRACTING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    BLOCKED = "BLOCKED"
    READY = "READY"
    IN_PRODUCTION_SIMULATED = "IN_PRODUCTION_SIMULATED"
    COMPLETED_SIMULATED = "COMPLETED_SIMULATED"


class DocumentStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FAILED = "FAILED"
    ACCEPTED = "ACCEPTED"


class ExtractionStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FAILED = "FAILED"
    ACCEPTED = "ACCEPTED"


class ProviderMode(StrEnum):
    CSV_DETERMINISTIC = "CSV_DETERMINISTIC"
    FIXTURE = "FIXTURE"
    LIVE = "LIVE"


class EvidenceStatus(StrEnum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    MISSING = "MISSING"


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class ReviewState(StrEnum):
    PENDING = "PENDING"
    CONFLICT = "CONFLICT"
    ACCEPTED = "ACCEPTED"
    EDITED = "EDITED"
    REJECTED = "REJECTED"


class CandidateKind(StrEnum):
    REQUIREMENT = "REQUIREMENT"
    BOM_LINE = "BOM_LINE"


class BomRevisionStatus(StrEnum):
    PUBLISHED = "PUBLISHED"
    SUPERSEDED = "SUPERSEDED"


class ArtifactType(StrEnum):
    SUPPLIER_SELECTION = "SUPPLIER_SELECTION"
    COMPATIBILITY_CHECK = "COMPATIBILITY_CHECK"
    WORK_INSTRUCTION = "WORK_INSTRUCTION"
    QC_PLAN = "QC_PLAN"


class ArtifactStatus(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    REVALIDATION_REQUIRED = "REVALIDATION_REQUIRED"
    SUPERSEDED = "SUPERSEDED"


class ChangeStatus(StrEnum):
    PROPOSED = "PROPOSED"
    PUBLISHED = "PUBLISHED"
    WITHDRAWN = "WITHDRAWN"


class ExceptionStatus(StrEnum):
    OPEN = "OPEN"
    IN_REVIEW = "IN_REVIEW"
    RESOLVED = "RESOLVED"
    WAIVED = "WAIVED"


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    STALE = "STALE"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


def values(e: type[StrEnum]) -> tuple[str, ...]:
    return tuple(m.value for m in e)
