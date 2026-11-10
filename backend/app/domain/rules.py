"""Explicit, machine-readable synthetic compatibility rules. Deterministic PASS / FAIL / UNKNOWN.

LLM output is never an input to these functions' verdicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.domain.bom import BomLine

Verdict = Literal["PASS", "FAIL", "UNKNOWN"]
CHECK_KINDS = ("ATTRIBUTE_EQUALS", "ATTRIBUTE_IN_LIST")


@dataclass(frozen=True)
class Rule:
    rule_code: str
    check_kind: str
    params: dict[str, Any]
    is_critical: bool = True
    description: str = ""

    def line_keys(self) -> list[str]:
        return sorted({ref["line_key"] for ref in self._refs()})

    def _refs(self) -> list[dict[str, Any]]:
        if self.check_kind == "ATTRIBUTE_EQUALS":
            return [self.params["left"], self.params["right"]]
        if self.check_kind == "ATTRIBUTE_IN_LIST":
            return [self.params["value"], self.params["allowed"]]
        return []

    @property
    def change_reason_code(self) -> str:
        return str(self.params.get("reason_code", "COMPATIBILITY_UNKNOWN"))


@dataclass(frozen=True)
class RuleResult:
    rule_code: str
    result: Verdict
    reason_code: str | None
    message: str
    evidence: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_code": self.rule_code,
            "result": self.result,
            "reason_code": self.reason_code,
            "message": self.message,
            "evidence": self.evidence,
        }


def validate_rule_definition(rule: Rule) -> list[str]:
    errs: list[str] = []
    if rule.check_kind not in CHECK_KINDS:
        errs.append(f"unsupported check_kind {rule.check_kind}")
        return errs
    for ref in rule._refs():
        if not isinstance(ref, dict) or not ref.get("line_key") or not ref.get("attribute"):
            errs.append("each reference needs line_key and attribute")
    return errs


def _lookup(lines: dict[str, BomLine], ref: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    line = lines.get(ref["line_key"])
    ev: dict[str, Any] = {"line_key": ref["line_key"], "attribute": ref["attribute"]}
    if line is None:
        ev["part_number"] = None
        return None, ev
    ev["part_number"] = line.part_number
    ev["part_revision"] = line.part_revision
    value = line.attributes.get(ref["attribute"])
    ev["value"] = value
    return value, ev


def evaluate(rule: Rule, lines_by_key: dict[str, BomLine]) -> RuleResult:
    errs = validate_rule_definition(rule)
    if errs:
        return RuleResult(rule.rule_code, "UNKNOWN", "COMPATIBILITY_UNKNOWN", "; ".join(errs), {})
    if rule.check_kind == "ATTRIBUTE_EQUALS":
        left, lev = _lookup(lines_by_key, rule.params["left"])
        right, rev = _lookup(lines_by_key, rule.params["right"])
        evidence = {"left": lev, "right": rev}
        if left is None or right is None:
            return RuleResult(
                rule.rule_code,
                "UNKNOWN",
                "COMPATIBILITY_UNKNOWN",
                "required attribute missing; compatibility cannot be determined",
                evidence,
            )
        if left == right:
            return RuleResult(rule.rule_code, "PASS", None, f"{left} == {right}", evidence)
        return RuleResult(
            rule.rule_code,
            "FAIL",
            rule.change_reason_code,
            f"{lev['line_key']}.{lev['attribute']}={left} does not match {rev['line_key']}.{rev['attribute']}={right}",
            evidence,
        )
    value, vev = _lookup(lines_by_key, rule.params["value"])
    allowed, aev = _lookup(lines_by_key, rule.params["allowed"])
    evidence = {"value": vev, "allowed": aev}
    if value is None or not isinstance(allowed, list):
        return RuleResult(
            rule.rule_code,
            "UNKNOWN",
            "COMPATIBILITY_UNKNOWN",
            "required attribute missing; compatibility cannot be determined",
            evidence,
        )
    if value in allowed:
        return RuleResult(rule.rule_code, "PASS", None, f"{value} in {allowed}", evidence)
    return RuleResult(
        rule.rule_code,
        "FAIL",
        rule.change_reason_code,
        f"{vev['line_key']}.{vev['attribute']}={value} not in {aev['line_key']}.{aev['attribute']}={allowed}",
        evidence,
    )
