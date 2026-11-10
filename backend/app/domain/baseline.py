"""Baseline snapshot hash. An approval is bound to exactly this value.

Inputs: BOM content hash, every current artifact (node, id, revision, content hash, status),
active dependency edges, compatibility rule definitions, and the rule engine version.
Any change to any input yields a different hash, which makes prior approvals stale.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BaselineInputs:
    bom_revision_id: str | None
    bom_content_hash: str | None
    artifacts: list[tuple[str, str, int, str, str]]  # node_key, id, revision, content_hash, status
    edges: list[tuple[str, str, str, str]]  # source, target, dependency_type, rule_code
    rules: list[tuple[str, str, str]]  # rule_code, check_kind, params_hash
    rule_version: str

    def snapshot(self) -> dict[str, Any]:
        return {
            "bom_revision_id": self.bom_revision_id,
            "bom_content_hash": self.bom_content_hash,
            "artifacts": [list(a) for a in sorted(self.artifacts)],
            "edges": [list(e) for e in sorted(self.edges)],
            "rules": [list(r) for r in sorted(self.rules)],
            "rule_version": self.rule_version,
        }


def baseline_hash(inputs: BaselineInputs) -> str:
    blob = json.dumps(inputs.snapshot(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def params_hash(params: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
