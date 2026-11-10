"""Typed extraction contract shared by every provider. Provider output must validate against this
schema; anything else is rejected (Invariant I-5: raw model output never touches domain state)."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "candidates-v1"
Confidence = Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_number: int | None = Field(default=None, ge=1)
    section: str | None = Field(default=None, max_length=200)
    exact_excerpt: str = Field(default="", max_length=2000)


class BomCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_key: str | None = Field(default=None, max_length=80)
    part_number: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=300)
    quantity: float | None = None
    unit: str | None = Field(default=None, max_length=20)
    part_revision: str | None = Field(default=None, max_length=20)
    attributes: dict[str, Any] = Field(default_factory=dict)
    evidence: Evidence = Field(default_factory=Evidence)
    confidence: Confidence = "UNKNOWN"


class RequirementCandidateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(max_length=120)
    value: str | float | int | bool | None
    unit: str | None = Field(default=None, max_length=32)
    evidence: Evidence = Field(default_factory=Evidence)
    confidence: Confidence = "UNKNOWN"


class ExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_name: str | None = None
    product_sku: str | None = None
    target_quantity: int | None = None
    requirements: list[RequirementCandidateOut] = Field(default_factory=list, max_length=200)
    bom_candidates: list[BomCandidate] = Field(default_factory=list, max_length=500)
    missing_fields: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)


class ModelMetadata(BaseModel):
    provider_mode: Literal["FIXTURE", "LIVE", "CSV_DETERMINISTIC"]
    model: str
    prompt_version: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None


class ExtractionResult(BaseModel):
    output: ExtractionOutput
    metadata: ModelMetadata
    raw_text: str | None = None
