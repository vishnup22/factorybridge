"""Extraction provider interface plus the deterministic fixture provider.

The fixture provider is a documented line-grammar parser (NOT an LLM) used for offline/CI runs.
Its output is labeled provider_mode=FIXTURE everywhere it is displayed.
"""

from __future__ import annotations

import re
from typing import Protocol

from app.services.extraction.schema import (
    BomCandidate,
    Evidence,
    ExtractionOutput,
    ExtractionResult,
    ModelMetadata,
    RequirementCandidateOut,
)

PROMPT_VERSION = "extract-v1"


class ProviderError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class ExtractionProvider(Protocol):
    mode: str
    model: str

    def extract(self, pages: list[str], request_id: str) -> ExtractionResult: ...


_BOM = re.compile(
    r"^BOM LINE\s+(?P<key>[A-Z0-9_]+)\s*:\s*(?P<part>[A-Za-z0-9\-]+)\s+rev\s+(?P<rev>[A-Za-z0-9]+)\s*,"
    r"\s*qty\s+(?P<qty>-?[0-9.]+)\s+(?P<unit>[A-Za-z]+)(?P<attrs>(?:\s*,\s*[a-z_]+=[^,|]+)*)"
    r"\s*(?:\|\s*(?P<desc>.+))?$"
)
_REQ = re.compile(r"^REQ\s+(?P<key>[a-z0-9_]+)\s*:\s*(?P<value>[^\s]+)(?:\s+(?P<unit>[A-Za-z%]+))?\s*$")
_SKU = re.compile(r"^Product SKU\s*:\s*(?P<v>\S+)\s*$")
_QTY = re.compile(r"^Target quantity\s*:\s*(?P<v>\d+)")
_NAME = re.compile(r"^Product\s*:\s*(?P<v>.+)$")


def _scalar(v: str) -> str | int | float:
    v = v.strip()
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except ValueError:
        return v


class FixtureProvider:
    mode = "FIXTURE"
    model = "fixture-deterministic-v1"

    def extract(self, pages: list[str], request_id: str) -> ExtractionResult:
        out = ExtractionOutput()
        for page_no, text in enumerate(pages, start=1):
            for raw in text.splitlines():
                line = raw.strip()
                if not line:
                    continue
                ev = Evidence(page_number=page_no, exact_excerpt=line)
                if m := _BOM.match(line):
                    attrs: dict[str, object] = {}
                    for pair in filter(None, (p.strip() for p in (m.group("attrs") or "").split(","))):
                        k, _, v = pair.partition("=")
                        val = v.strip()
                        attrs[k.strip()] = (
                            [x for x in val.split(";") if x] if k.strip() == "accepted_mounts" else _scalar(val)
                        )
                    out.bom_candidates.append(
                        BomCandidate(
                            line_key=m.group("key"),
                            part_number=m.group("part"),
                            part_revision=m.group("rev"),
                            quantity=float(m.group("qty")),
                            unit=m.group("unit"),
                            description=(m.group("desc") or "").strip() or None,
                            attributes=attrs,
                            evidence=ev,
                            confidence="HIGH",
                        )
                    )
                elif m := _REQ.match(line):
                    out.requirements.append(
                        RequirementCandidateOut(
                            key=m.group("key"),
                            value=_scalar(m.group("value")),
                            unit=m.group("unit"),
                            evidence=ev,
                            confidence="HIGH",
                        )
                    )
                elif m := _SKU.match(line):
                    out.product_sku = m.group("v")
                elif m := _QTY.match(line):
                    out.target_quantity = int(m.group("v"))
                elif (m := _NAME.match(line)) and out.project_name is None:
                    out.project_name = m.group("v").strip()
        if out.product_sku is None:
            out.missing_fields.append("product_sku")
        if out.target_quantity is None:
            out.missing_fields.append("target_quantity")
        return ExtractionResult(
            output=out,
            metadata=ModelMetadata(provider_mode="FIXTURE", model=self.model, prompt_version=PROMPT_VERSION),
        )


def get_provider() -> ExtractionProvider:
    from app.core.config import get_settings

    s = get_settings()
    if s.ai_provider == "anthropic":
        from app.integrations.anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    return FixtureProvider()
