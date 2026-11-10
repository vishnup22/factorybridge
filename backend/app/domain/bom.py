"""Pure BOM logic: normalization, validation, canonical hashing, stable-key diff, change application.

No database, HTTP, or AI dependencies. Every function is deterministic.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

UNIT_ALIASES: dict[str, str] = {
    "each": "each",
    "ea": "each",
    "pc": "each",
    "pcs": "each",
    "piece": "each",
    "pieces": "each",
    "unit": "each",
    "units": "each",
    "set": "set",
    "sets": "set",
    "m": "m",
    "meter": "m",
    "meters": "m",
    "mm": "mm",
    "kg": "kg",
    "g": "g",
}

# Explicit attribute type schema. Unknown attributes are allowed if they are scalar strings/numbers.
ATTRIBUTE_TYPES: dict[str, str] = {
    "voltage_v": "number",
    "output_voltage_v": "number",
    "input_voltage_v": "number",
    "torque_nm": "number",
    "current_a": "number",
    "mount_pattern": "string",
    "connector": "string",
    "accepted_mounts": "string_list",
}


@dataclass(frozen=True)
class BomLine:
    line_key: str
    part_number: str
    description: str
    quantity: Decimal
    unit: str
    part_revision: str
    attributes: dict[str, Any] = field(default_factory=dict)

    def canonical(self) -> dict[str, Any]:
        return {
            "line_key": self.line_key,
            "part_number": self.part_number,
            "description": self.description,
            "quantity": format_quantity(self.quantity),
            "unit": self.unit,
            "part_revision": self.part_revision,
            "attributes": canonical_attributes(self.attributes),
        }


@dataclass(frozen=True)
class BomIssue:
    code: str
    message: str
    line_key: str | None = None
    field: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "line_key": self.line_key, "field": self.field}


def format_quantity(q: Decimal) -> str:
    normalized = q.normalize()
    # Avoid scientific notation (e.g. Decimal('1E+2')).
    return format(normalized, "f")


def canonical_attributes(attrs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in sorted(attrs):
        v = attrs[k]
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        if isinstance(v, list):
            v = sorted(str(x) for x in v)
        out[k] = v
    return out


def normalize_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    return UNIT_ALIASES.get(unit.strip().lower())


def parse_quantity(raw: Any) -> Decimal | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        q = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        return None
    if not q.is_finite():
        return None
    return q


def coerce_line(raw: dict[str, Any]) -> tuple[BomLine | None, list[BomIssue]]:
    """Validate and normalize one raw BOM line dict. Returns (line, issues)."""
    issues: list[BomIssue] = []
    line_key = str(raw.get("line_key") or "").strip().upper()
    if not line_key:
        issues.append(BomIssue("MISSING_LINE_KEY", "line_key is required", None, "line_key"))
    part_number = str(raw.get("part_number") or "").strip()
    if not part_number:
        issues.append(BomIssue("MISSING_PART_NUMBER", "part_number is required", line_key or None, "part_number"))
    part_revision = str(raw.get("part_revision") or "").strip()
    if not part_revision:
        issues.append(BomIssue("MISSING_PART_REVISION", "part_revision is required", line_key or None, "part_revision"))
    quantity = parse_quantity(raw.get("quantity"))
    if quantity is None:
        issues.append(BomIssue("INVALID_QUANTITY", "quantity must be a number", line_key or None, "quantity"))
    elif quantity <= 0:
        issues.append(BomIssue("NON_POSITIVE_QUANTITY", "quantity must be > 0", line_key or None, "quantity"))
    unit = normalize_unit(raw.get("unit"))
    if unit is None:
        issues.append(BomIssue("UNIT_INVALID", f"unsupported unit {raw.get('unit')!r}", line_key or None, "unit"))
    elif unit == "each" and quantity is not None and quantity != quantity.to_integral_value():
        issues.append(
            BomIssue("UNIT_INCOMPATIBLE", "discrete unit 'each' requires an integer quantity", line_key, "quantity")
        )
    attributes = raw.get("attributes") or {}
    if not isinstance(attributes, dict):
        issues.append(BomIssue("ATTRIBUTE_TYPE_INVALID", "attributes must be an object", line_key, "attributes"))
        attributes = {}
    issues.extend(validate_attributes(line_key, attributes))
    if issues:
        return None, issues
    assert quantity is not None and unit is not None
    return (
        BomLine(
            line_key=line_key,
            part_number=part_number,
            description=str(raw.get("description") or "").strip(),
            quantity=quantity,
            unit=unit,
            part_revision=part_revision,
            attributes=canonical_attributes(attributes),
        ),
        [],
    )


def validate_attributes(line_key: str, attributes: dict[str, Any]) -> list[BomIssue]:
    issues: list[BomIssue] = []
    for name, value in attributes.items():
        expected = ATTRIBUTE_TYPES.get(name)
        ok: bool
        if expected == "number":
            ok = isinstance(value, int | float) and not isinstance(value, bool)
        elif expected == "string":
            ok = isinstance(value, str) and bool(value.strip())
        elif expected == "string_list":
            ok = isinstance(value, list) and all(isinstance(x, str) for x in value)
        else:
            ok = isinstance(value, str | int | float) and not isinstance(value, bool)
        if not ok:
            issues.append(
                BomIssue(
                    "ATTRIBUTE_TYPE_INVALID",
                    f"attribute {name!r} must be {expected or 'a scalar'}",
                    line_key or None,
                    f"attributes.{name}",
                )
            )
    return issues


def validate_bom(raw_lines: list[dict[str, Any]]) -> tuple[list[BomLine], list[BomIssue]]:
    lines: list[BomLine] = []
    issues: list[BomIssue] = []
    seen: set[str] = set()
    if not raw_lines:
        issues.append(BomIssue("EMPTY_BOM", "BOM must contain at least one line"))
    for raw in raw_lines:
        line, line_issues = coerce_line(raw)
        issues.extend(line_issues)
        if line is None:
            continue
        if line.line_key in seen:
            issues.append(BomIssue("DUPLICATE_LINE_KEY", f"duplicate line_key {line.line_key}", line.line_key))
            continue
        seen.add(line.line_key)
        lines.append(line)
    return sorted(lines, key=lambda x: x.line_key), issues


def content_hash(lines: list[BomLine]) -> str:
    canonical = [line.canonical() for line in sorted(lines, key=lambda x: x.line_key)]
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode()).hexdigest()


ChangeType = Literal["ADDED", "REMOVED", "UPDATED", "UNCHANGED"]


@dataclass(frozen=True)
class LineDiff:
    line_key: str
    change_type: ChangeType
    old: dict[str, Any] | None
    new: dict[str, Any] | None
    changed_fields: tuple[str, ...]

    @property
    def reason_codes(self) -> list[str]:
        codes: list[str] = []
        if self.change_type in ("ADDED", "REMOVED") or "part_number" in self.changed_fields:
            codes.append("BOM_PART_CHANGED")
        if "quantity" in self.changed_fields or "unit" in self.changed_fields:
            codes.append("BOM_QUANTITY_CHANGED")
        if "part_revision" in self.changed_fields:
            codes.append("PART_REVISION_CHANGED")
        if any(f.startswith("attributes.") for f in self.changed_fields) and not codes:
            codes.append("BOM_PART_CHANGED")
        return codes

    def as_dict(self) -> dict[str, Any]:
        return {
            "line_key": self.line_key,
            "change_type": self.change_type,
            "old": self.old,
            "new": self.new,
            "changed_fields": list(self.changed_fields),
            "reason_codes": self.reason_codes,
        }


def _changed_fields(old: dict[str, Any], new: dict[str, Any]) -> tuple[str, ...]:
    fields: list[str] = []
    for f in ("part_number", "description", "quantity", "unit", "part_revision"):
        if old[f] != new[f]:
            fields.append(f)
    oa, na = old["attributes"], new["attributes"]
    for k in sorted(set(oa) | set(na)):
        if oa.get(k) != na.get(k):
            fields.append(f"attributes.{k}")
    return tuple(fields)


def diff(old_lines: list[BomLine], new_lines: list[BomLine]) -> list[LineDiff]:
    old = {line.line_key: line.canonical() for line in old_lines}
    new = {line.line_key: line.canonical() for line in new_lines}
    out: list[LineDiff] = []
    for key in sorted(set(old) | set(new)):
        o, n = old.get(key), new.get(key)
        if o is None:
            out.append(LineDiff(key, "ADDED", None, n, ()))
        elif n is None:
            out.append(LineDiff(key, "REMOVED", o, None, ()))
        else:
            fields = _changed_fields(o, n)
            out.append(LineDiff(key, "UPDATED" if fields else "UNCHANGED", o, n, fields))
    return out


class ChangeSetError(ValueError):
    def __init__(self, issues: list[BomIssue]):
        super().__init__("; ".join(i.message for i in issues))
        self.issues = issues


def line_to_raw(line: BomLine) -> dict[str, Any]:
    c = line.canonical()
    c["quantity"] = str(line.quantity)
    return c


def apply_change_set(base: list[BomLine], operations: list[dict[str, Any]]) -> list[BomLine]:
    """Apply UPDATE/ADD/REMOVE operations keyed by stable line_key. Raises ChangeSetError."""
    working: dict[str, dict[str, Any]] = {line.line_key: line_to_raw(line) for line in base}
    issues: list[BomIssue] = []
    touched: set[str] = set()
    for op in operations:
        kind = str(op.get("operation", "")).upper()
        key = str(op.get("line_key") or "").strip().upper()
        if not key:
            issues.append(BomIssue("MISSING_LINE_KEY", "operation requires line_key"))
            continue
        if key in touched:
            issues.append(BomIssue("DUPLICATE_OPERATION", f"line {key} modified twice in one change", key))
            continue
        touched.add(key)
        new = op.get("new") or {}
        if kind == "UPDATE":
            if key not in working:
                issues.append(BomIssue("UNKNOWN_LINE", f"cannot update missing line {key}", key))
                continue
            merged = dict(working[key])
            for f in ("part_number", "description", "quantity", "unit", "part_revision", "attributes"):
                if f in new:
                    merged[f] = new[f]
            working[key] = merged
        elif kind == "ADD":
            if key in working:
                issues.append(BomIssue("LINE_EXISTS", f"line {key} already exists", key))
                continue
            working[key] = {**new, "line_key": key}
        elif kind == "REMOVE":
            if key not in working:
                issues.append(BomIssue("UNKNOWN_LINE", f"cannot remove missing line {key}", key))
                continue
            del working[key]
        else:
            issues.append(BomIssue("INVALID_OPERATION", f"unsupported operation {kind!r}", key))
    if issues:
        raise ChangeSetError(issues)
    lines, v_issues = validate_bom(list(working.values()))
    if v_issues:
        raise ChangeSetError(v_issues)
    if content_hash(lines) == content_hash(base):
        raise ChangeSetError([BomIssue("NO_EFFECTIVE_CHANGE", "change set does not modify the BOM")])
    return lines
