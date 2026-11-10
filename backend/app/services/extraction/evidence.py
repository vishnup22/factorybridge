"""Server-side evidence verification. A candidate is VERIFIED only if its quoted excerpt appears on
the cited page (after whitespace/quote normalization) AND its key values appear in that excerpt."""

from __future__ import annotations

from typing import Any

from app.services.extraction.parsers import normalize_text
from app.services.extraction.schema import BomCandidate, Evidence, RequirementCandidateOut


def _format_num(v: float | int) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def verify_excerpt(evidence: Evidence, pages: list[str]) -> tuple[str, list[dict[str, Any]]]:
    excerpt = normalize_text(evidence.exact_excerpt or "")
    if not excerpt:
        return "MISSING", [{"code": "EVIDENCE_MISSING", "message": "No source excerpt provided"}]
    if evidence.page_number is None or not (1 <= evidence.page_number <= len(pages)):
        return "UNVERIFIED", [{"code": "EVIDENCE_PAGE_INVALID", "message": "Cited page does not exist"}]
    if excerpt not in normalize_text(pages[evidence.page_number - 1]):
        return "UNVERIFIED", [
            {"code": "EVIDENCE_NOT_FOUND", "message": f"Excerpt not found on page {evidence.page_number}"}
        ]
    return "VERIFIED", []


def verify_bom_candidate(c: BomCandidate, pages: list[str]) -> tuple[str, list[dict[str, Any]]]:
    status, issues = verify_excerpt(c.evidence, pages)
    if status != "VERIFIED":
        return status, issues
    excerpt = normalize_text(c.evidence.exact_excerpt)
    for label, value in (("part_number", c.part_number), ("quantity", c.quantity)):
        if value is None:
            continue
        token = _format_num(value) if isinstance(value, int | float) else str(value)
        if normalize_text(token) not in excerpt:
            issues.append(
                {"code": "VALUE_NOT_IN_EXCERPT", "message": f"{label}={token} is not supported by the excerpt"}
            )
    return ("UNVERIFIED" if issues else "VERIFIED"), issues


def verify_requirement(r: RequirementCandidateOut, pages: list[str]) -> tuple[str, list[dict[str, Any]]]:
    status, issues = verify_excerpt(r.evidence, pages)
    if status == "VERIFIED" and r.value is not None:
        token = (
            _format_num(r.value) if isinstance(r.value, int | float) and not isinstance(r.value, bool) else str(r.value)
        )
        if normalize_text(token) not in normalize_text(r.evidence.exact_excerpt):
            return "UNVERIFIED", [{"code": "VALUE_NOT_IN_EXCERPT", "message": f"value {token} not in excerpt"}]
    return status, issues
