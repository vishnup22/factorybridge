from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from demo.seed import seed_org
from fastapi.testclient import TestClient
from sqlalchemy import text

from alembic import command
from app.core.db import reset_engine, session_factory
from app.models import Base, OutboxEvent
from app.services import outbox
from app.services.extraction import pipeline

BACKEND = Path(__file__).resolve().parents[2]
FIXTURES = BACKEND.parent / "demo" / "fixtures"
PASSWORD = "integration-test-password"


@pytest.fixture(scope="session", autouse=True)
def migrated() -> None:
    reset_engine()
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def clean_db(migrated: None) -> None:
    tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    with session_factory()() as db:
        db.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        seed_org(db, password=PASSWORD)
        db.commit()


class Api:
    """Thin test client wrapper that logs in per role and runs worker work inline."""

    def __init__(self, client: TestClient):
        self.client = client
        self.tokens: dict[str, str] = {}

    def h(self, who: str, **extra: str) -> dict[str, str]:
        if who not in self.tokens:
            r = self.client.post("/api/v1/auth/login", json={"email": f"{who}@demo.example", "password": PASSWORD})
            assert r.status_code == 200, r.text
            self.tokens[who] = r.json()["access_token"]
        return {"Authorization": f"Bearer {self.tokens[who]}", **extra}

    def get(self, who: str, path: str, **kw: Any):
        return self.client.get(f"/api/v1{path}", headers=self.h(who), **kw)

    def post(self, who: str, path: str, json: Any = None, headers: dict[str, str] | None = None, **kw: Any):
        return self.client.post(f"/api/v1{path}", json=json, headers={**self.h(who), **(headers or {})}, **kw)

    # ---- workflow helpers ----
    def create_project(self, who: str = "customer") -> str:
        r = self.post(
            who,
            "/projects",
            {
                "name": "Warehouse Scout V1",
                "sku": "WS-100",
                "target_quantity": 100,
                "description": "Synthetic 100-unit batch",
            },
        )
        assert r.status_code == 201, r.text
        return r.json()["data"]["id"]

    def upload(self, pid: str, name: str, who: str = "customer", data: bytes | None = None):
        payload = data if data is not None else (FIXTURES / name).read_bytes()
        return self.client.post(
            f"/api/v1/projects/{pid}/documents",
            headers=self.h(who),
            files={"file": (name, payload, "application/octet-stream")},
        )

    def run_worker(self, provider: Any = None) -> int:
        def publish(ev: OutboxEvent) -> None:
            if ev.event_type == "DocumentUploaded":
                pipeline.process_job(uuid.UUID(ev.payload_json["job_id"]), provider=provider)

        with session_factory()() as db:
            return outbox.dispatch_pending(db, publish)

    def accept_all(self, doc_id: str, reason: str = "Reviewed against source", headers: dict[str, str] | None = None):
        ex = self.get("engineer", f"/documents/{doc_id}/extraction").json()["data"]
        decisions = [{"candidate_id": c["id"], "action": "ACCEPT"} for c in ex["candidates"]]
        return self.post(
            "engineer",
            f"/documents/{doc_id}/extraction/accept",
            {"decisions": decisions, "reason": reason},
            headers=headers,
        )

    def project(self, pid: str, who: str = "engineer") -> dict[str, Any]:
        r = self.get(who, f"/projects/{pid}")
        assert r.status_code == 200, r.text
        return r.json()["data"]

    def r1_with_plan(self) -> str:
        pid = self.create_project()
        doc = self.upload(pid, "bom_v1.csv").json()["data"]
        self.run_worker()
        assert self.accept_all(doc["id"]).status_code == 200
        r = self.post("engineer", f"/projects/{pid}/plan/apply-template", {"template": "warehouse_scout_v1"})
        assert r.status_code == 200, r.text
        return pid

    def request_approval(self, pid: str, who: str = "engineer", **body: Any):
        p = self.project(pid)
        return self.post(
            who, f"/projects/{pid}/approvals/request", {"expected_baseline_version": p["baseline_version"], **body}
        )

    def assignee(self, pid: str, approval_id: str) -> str:
        rows = self.get("admin", f"/projects/{pid}/approvals").json()["data"]
        return next(a for a in rows if a["id"] == approval_id)["assigned_approver_email"].split("@")[0]

    def approve(
        self,
        pid: str,
        approval_id: str,
        who: str | None = None,
        decision: str = "APPROVE",
        headers: dict[str, str] | None = None,
        **body: Any,
    ):
        who = who or self.assignee(pid, approval_id)
        return self.post(
            who,
            f"/projects/{pid}/approvals/{approval_id}/decision",
            {"decision": decision, "reason": "Reviewed baseline and checklist", **body},
            headers=headers,
        )

    def ready_r1(self) -> tuple[str, str]:
        pid = self.r1_with_plan()
        a = self.request_approval(pid)
        assert a.status_code == 201, a.text
        aid = a.json()["data"]["id"]
        d = self.approve(pid, aid)
        assert d.status_code == 200, d.text
        assert self.project(pid)["status"] == "READY"
        return pid, aid

    def propose(self, pid: str, ops: list[dict[str, Any]], who: str = "engineer", base: int | None = None):
        p = self.project(pid)
        return self.post(
            who,
            f"/projects/{pid}/changes",
            {
                "base_revision_number": base or p["current_revision_number"],
                "reason": "Customer revised drive actuator specifications",
                "changes": ops,
            },
        )

    def publish(self, pid: str, cid: str, headers: dict[str, str] | None = None, **body: Any):
        return self.post("engineer", f"/projects/{pid}/changes/{cid}/publish", body, headers=headers)

    def exceptions(self, pid: str, status: str = "OPEN") -> list[dict[str, Any]]:
        return self.get("engineer", f"/projects/{pid}/exceptions", params={"status": status}).json()["data"]

    def exc_for(self, pid: str, node: str) -> dict[str, Any]:
        return next(e for e in self.exceptions(pid) if e["node_key"] == node)

    def resolve(
        self, pid: str, node: str, content: dict[str, Any] | None, note: str = "Revalidated against current BOM"
    ):
        e = self.exc_for(pid, node)
        return self.post(
            "engineer",
            f"/projects/{pid}/exceptions/{e['id']}/resolve",
            {"resolution_note": note, "artifact_content": content},
        )

    def artifact(self, pid: str, node: str) -> dict[str, Any]:
        rows = self.get("engineer", f"/projects/{pid}/artifacts", params={"node_key": node}).json()["data"]
        return rows[0]


@pytest.fixture
def api() -> Api:
    from app.main import app

    with TestClient(app) as client:
        yield Api(client)
