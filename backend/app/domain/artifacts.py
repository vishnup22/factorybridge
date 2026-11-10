"""Deterministic validators for downstream manufacturing artifacts.

Each validator compares an artifact's declared references to the *current* BOM / catalog / other
artifacts. An artifact is VALID only if every reference matches exactly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.domain.bom import BomLine
from app.domain.rules import Rule, evaluate


@dataclass(frozen=True)
class SupplierFacts:
    id: str
    part_number: str
    part_revision: str | None
    vendor_name: str
    available_qty: int
    lead_days: int
    approved_for_part: bool


@dataclass(frozen=True)
class ArtifactFacts:
    node_key: str
    revision: int
    status: str


@dataclass
class ValidationContext:
    lines: dict[str, BomLine]
    target_quantity: int
    suppliers: dict[str, SupplierFacts] = field(default_factory=dict)
    rules: dict[str, Rule] = field(default_factory=dict)
    artifacts: dict[str, ArtifactFacts] = field(default_factory=dict)


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    field: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "field": self.field}


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    findings: list[Finding]
    computed_content: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "findings": [f.as_dict() for f in self.findings]}


def artifact_content_hash(content: dict[str, Any]) -> str:
    blob = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _check_line_references(content: dict[str, Any], ctx: ValidationContext) -> list[Finding]:
    refs = content.get("references")
    findings: list[Finding] = []
    if not isinstance(refs, dict) or not refs:
        return [Finding("REFERENCES_REQUIRED", "artifact must reference at least one BOM line", "references")]
    for line_key, expected in sorted(refs.items()):
        line = ctx.lines.get(line_key)
        if line is None:
            findings.append(Finding("REFERENCED_LINE_MISSING", f"BOM line {line_key} not in current revision"))
            continue
        if not isinstance(expected, dict):
            findings.append(Finding("REFERENCE_INVALID", f"reference for {line_key} must be an object"))
            continue
        for f in ("part_number", "part_revision"):
            if f in expected and expected[f] != getattr(line, f):
                findings.append(
                    Finding(
                        "REFERENCE_STALE",
                        f"{line_key}.{f}: artifact says {expected[f]!r}, BOM says {getattr(line, f)!r}",
                        f"references.{line_key}.{f}",
                    )
                )
        if "part_number" not in expected:
            findings.append(Finding("REFERENCE_INVALID", f"reference for {line_key} must pin part_number"))
        for attr, value in sorted((expected.get("attributes") or {}).items()):
            actual = line.attributes.get(attr)
            if actual != value:
                findings.append(
                    Finding(
                        "REFERENCE_STALE",
                        f"{line_key}.attributes.{attr}: artifact says {value!r}, BOM says {actual!r}",
                        f"references.{line_key}.attributes.{attr}",
                    )
                )
    return findings


def _check_artifact_refs(content: dict[str, Any], ctx: ValidationContext) -> list[Finding]:
    findings: list[Finding] = []
    for node_key, revision in sorted((content.get("artifact_refs") or {}).items()):
        current = ctx.artifacts.get(node_key)
        if current is None:
            findings.append(Finding("ARTIFACT_REF_MISSING", f"referenced artifact {node_key} does not exist"))
        elif current.revision != revision:
            findings.append(
                Finding(
                    "ARTIFACT_REF_STALE",
                    f"{node_key}: references revision {revision}, current is {current.revision}",
                )
            )
        elif current.status != "VALID":
            findings.append(Finding("ARTIFACT_REF_NOT_VALID", f"{node_key} revision {revision} is {current.status}"))
    return findings


def validate_supplier_selection(content: dict[str, Any], ctx: ValidationContext) -> ValidationResult:
    findings: list[Finding] = []
    line_key = content.get("line_key")
    option_id = str(content.get("supplier_option_id") or "")
    line = ctx.lines.get(str(line_key))
    option = ctx.suppliers.get(option_id)
    if line is None:
        findings.append(Finding("REFERENCED_LINE_MISSING", f"BOM line {line_key} not in current revision"))
    if option is None:
        findings.append(Finding("SUPPLIER_OPTION_MISSING", "supplier option not found in synthetic catalog"))
    if line is not None and option is not None:
        if option.part_number != line.part_number:
            findings.append(
                Finding(
                    "SUPPLIER_PART_MISMATCH",
                    f"option supplies {option.part_number}, BOM requires {line.part_number}",
                )
            )
        if option.part_revision is not None and option.part_revision != line.part_revision:
            findings.append(
                Finding(
                    "SUPPLIER_REVISION_MISMATCH",
                    f"option is rev {option.part_revision}, BOM requires rev {line.part_revision}",
                )
            )
        if not option.approved_for_part:
            findings.append(Finding("SUPPLIER_NOT_APPROVED", f"{option.vendor_name} not approved for part"))
        required = line.quantity * Decimal(ctx.target_quantity)
        if Decimal(option.available_qty) < required:
            findings.append(
                Finding(
                    "SUPPLIER_INSUFFICIENT_QTY",
                    f"{option.vendor_name} has {option.available_qty}, batch requires {required}",
                )
            )
    if content.get("part_number") and line is not None and content["part_number"] != line.part_number:
        findings.append(Finding("REFERENCE_STALE", "selection part_number does not match BOM"))
    computed = None
    if option is not None:
        computed = {
            "line_key": line_key,
            "supplier_option_id": option.id,
            "vendor_name": option.vendor_name,
            "part_number": option.part_number,
            "part_revision": line.part_revision if line else None,
            "lead_days": option.lead_days,
            "note": content.get("note", ""),
        }
    return ValidationResult(not findings, findings, computed)


def validate_compatibility_check(content: dict[str, Any], ctx: ValidationContext) -> ValidationResult:
    rule_code = content.get("rule_code")
    rule = ctx.rules.get(str(rule_code))
    if rule is None:
        f = Finding("COMPATIBILITY_UNKNOWN", f"no explicit rule registered for {rule_code}")
        return ValidationResult(False, [f], {"rule_code": rule_code, "result": "UNKNOWN"})
    result = evaluate(rule, ctx.lines)
    computed = result.as_dict()
    if result.result == "PASS":
        return ValidationResult(True, [], computed)
    code = "COMPATIBILITY_UNKNOWN" if result.result == "UNKNOWN" else "COMPATIBILITY_FAIL"
    return ValidationResult(False, [Finding(code, result.message)], computed)


def _validate_document(content: dict[str, Any], ctx: ValidationContext, list_field: str) -> ValidationResult:
    findings: list[Finding] = []
    if not str(content.get("document_code") or "").strip():
        findings.append(Finding("FIELD_REQUIRED", "document_code is required", "document_code"))
    items = content.get(list_field)
    if not isinstance(items, list) or not items:
        findings.append(Finding("FIELD_REQUIRED", f"{list_field} must be a non-empty list", list_field))
    findings.extend(_check_line_references(content, ctx))
    findings.extend(_check_artifact_refs(content, ctx))
    return ValidationResult(not findings, findings, None)


def validate_work_instruction(content: dict[str, Any], ctx: ValidationContext) -> ValidationResult:
    return _validate_document(content, ctx, "steps")


def _same_value(a: Any, b: Any) -> bool:
    numeric = all(isinstance(v, int | float) and not isinstance(v, bool) for v in (a, b))
    if numeric:
        return float(a) == float(b)
    return a == b


def validate_qc_plan(content: dict[str, Any], ctx: ValidationContext) -> ValidationResult:
    res = _validate_document(content, ctx, "checks")
    findings = list(res.findings)
    refs = content.get("references") or {}
    pinned: dict[str, list[Any]] = {}
    if isinstance(refs, dict):
        for ref in refs.values():
            attrs = ref.get("attributes") if isinstance(ref, dict) else None
            if isinstance(attrs, dict):
                for name, value in attrs.items():
                    pinned.setdefault(name, []).append(value)
    for i, check in enumerate(content.get("checks") or []):
        if not isinstance(check, dict) or not check.get("name") or "expected" not in check:
            findings.append(Finding("QC_CHECK_INVALID", f"checks[{i}] needs name and expected", f"checks[{i}]"))
            continue
        # A check on a pinned attribute must expect the pinned (and therefore current-baseline) value.
        values = pinned.get(str(check.get("parameter") or ""))
        if values and not all(_same_value(check["expected"], v) for v in values):
            findings.append(
                Finding(
                    "QC_CHECK_STALE",
                    f"checks[{i}] '{check['name']}' expects {check['expected']!r} but referenced parts specify "
                    f"{check['parameter']}={values[0]!r}",
                    f"checks[{i}].expected",
                )
            )
    return ValidationResult(not findings, findings, None)


VALIDATORS = {
    "SUPPLIER_SELECTION": validate_supplier_selection,
    "COMPATIBILITY_CHECK": validate_compatibility_check,
    "WORK_INSTRUCTION": validate_work_instruction,
    "QC_PLAN": validate_qc_plan,
}


def validate_artifact(artifact_type: str, content: dict[str, Any], ctx: ValidationContext) -> ValidationResult:
    validator = VALIDATORS.get(artifact_type)
    if validator is None:
        return ValidationResult(False, [Finding("UNKNOWN_ARTIFACT_TYPE", artifact_type)])
    return validator(content, ctx)
