"""End-to-end flagship demo / smoke test against a RUNNING stack (real API + Celery worker + outbox).

Usage (from backend/):
  uv run python ../scripts/demo_flow.py --base-url http://localhost:8000 --credentials var/demo_credentials.txt
  uv run python ../scripts/demo_flow.py --base-url https://api.example.com --smoke   # read-only checks only

Credentials file format: one "email  password" per line (written by `python -m demo.seed`), or set FB_DEMO_PASSWORD.
Exits non-zero on the first failed expectation. Creates a NEW synthetic project each run; never deletes anything.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "demo" / "fixtures"
SCENARIO = json.loads((FIXTURES / "flagship_scenario.json").read_text(encoding="utf-8"))


class Fail(Exception):
    pass


def expect(cond: bool, msg: str) -> None:
    if not cond:
        raise Fail(msg)
    print(f"  ok  {msg}")


class Client:
    def __init__(self, base: str, creds: dict[str, str]):
        self.http = httpx.Client(base_url=base.rstrip("/") + "/api/v1", timeout=30)
        self.creds = creds
        self.tokens: dict[str, str] = {}

    def _h(self, who: str) -> dict[str, str]:
        if who not in self.tokens:
            email = f"{who}@demo.example"
            r = self.http.post("/auth/login", json={"email": email, "password": self.creds[email]})
            if r.status_code != 200:
                raise Fail(f"login {email}: {r.status_code} {r.text[:200]}")
            self.tokens[who] = r.json()["access_token"]
        return {"Authorization": f"Bearer {self.tokens[who]}"}

    def call(
        self, who: str, method: str, path: str, expect_status: int | tuple[int, ...] = (200, 201, 202), **kw: Any
    ) -> Any:
        headers = {**self._h(who), **kw.pop("headers", {})}
        r = self.http.request(method, path, headers=headers, **kw)
        allowed = (expect_status,) if isinstance(expect_status, int) else expect_status
        if r.status_code not in allowed:
            raise Fail(f"{method} {path} as {who}: expected {allowed}, got {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else None

    def project(self, pid: str) -> dict[str, Any]:
        return self.call("engineer", "GET", f"/projects/{pid}")["data"]


def load_creds(path: str | None) -> dict[str, str]:
    pw = os.environ.get("FB_DEMO_PASSWORD")
    emails = ["customer", "engineer", "engineer2", "approver", "approver2", "admin"]
    if pw:
        return {f"{e}@demo.example": pw for e in emails}
    if not path or not Path(path).exists():
        raise SystemExit("Provide --credentials <file> or FB_DEMO_PASSWORD")
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2:
            out[parts[0]] = parts[1]
    return out


def smoke(base: str) -> None:
    print("smoke checks")
    with httpx.Client(base_url=base.rstrip("/"), timeout=15) as h:
        live = h.get("/api/v1/health/live")
        expect(live.status_code == 200, f"/health/live 200 ({live.status_code})")
        ready = h.get("/api/v1/health/ready", headers={"X-Health-Token": os.environ.get("HEALTH_TOKEN", "")})
        expect(ready.status_code == 200, f"/health/ready 200 ({ready.status_code}: {ready.text[:200]})")
        expect(h.get("/api/v1/projects").status_code == 401, "unauthenticated /projects is 401")
        if base.startswith("https://"):
            expect("strict-transport-security" in live.headers, "HSTS header present over HTTPS")


def wait_for_document(c: Client, doc_id: str, timeout: float = 90) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        d = c.call("engineer", "GET", f"/documents/{doc_id}")["data"]
        if d["processing_status"] in ("REVIEW_REQUIRED", "FAILED"):
            return d
        time.sleep(1.0)
    raise Fail(f"document {doc_id} not processed within {timeout}s (is the worker running?)")


def approve_current(c: Client, pid: str) -> dict[str, Any]:
    p = c.project(pid)
    a = c.call(
        "engineer",
        "POST",
        f"/projects/{pid}/approvals/request",
        json={"expected_baseline_version": p["baseline_version"], "note": "Demo release request"},
    )["data"]
    who = a["assigned_approver_email"].split("@")[0]
    c.call(
        who,
        "POST",
        f"/projects/{pid}/approvals/{a['id']}/decision",
        json={
            "decision": "APPROVE",
            "reason": "Reviewed baseline, artifacts and exceptions",
            "expected_baseline_hash": a["baseline_hash"],
        },
    )
    return a


def flagship(c: Client, source: str) -> str:
    print("1. customer creates project and uploads requirements")
    pid = c.call(
        "customer",
        "POST",
        "/projects",
        json={
            "name": "Warehouse Scout V1",
            "sku": "WS-100",
            "target_quantity": 100,
            "description": "Synthetic 100-unit pilot batch (demo run)",
        },
    )["data"]["id"]
    name = "requirements_v1.pdf" if source == "pdf" else "bom_v1.csv"
    doc = c.call(
        "customer",
        "POST",
        f"/projects/{pid}/documents",
        files={"file": (name, (FIXTURES / name).read_bytes(), "application/octet-stream")},
    )["data"]
    expect(doc["job"] is not None, f"upload accepted, job {doc['job']['status'] if doc['job'] else '-'}")

    print("2. worker extracts; engineer reviews evidence and publishes R1")
    d = wait_for_document(c, doc["id"])
    expect(d["processing_status"] == "REVIEW_REQUIRED", f"extraction ready for review ({d['processing_status']})")
    ex = c.call("engineer", "GET", f"/documents/{doc['id']}/extraction")["data"]
    bom = [x for x in ex["candidates"] if x["kind"] == "BOM_LINE"]
    expect(
        len(bom) == 5 and all(x["evidence_status"] == "VERIFIED" for x in bom),
        "5 BOM candidates with verified evidence",
    )
    decisions = [{"candidate_id": x["id"], "action": "ACCEPT"} for x in ex["candidates"]]
    r = c.call(
        "engineer",
        "POST",
        f"/documents/{doc['id']}/extraction/accept",
        json={"decisions": decisions, "reason": "Reviewed each value against cited source"},
    )["data"]
    expect(r["bom_revision"] == 1, "R1 published from reviewed extraction")

    print("3. apply manufacturing plan, approve R1")
    c.call("engineer", "POST", f"/projects/{pid}/plan/apply-template", json={"template": "warehouse_scout_v1"})
    expect(c.project(pid)["status"] == "REVIEW_REQUIRED", "plan valid; awaiting approval")
    a1 = approve_current(c, pid)
    expect(c.project(pid)["status"] == "READY", "R1 READY")

    print("4. EC-001: ACT-100 24V/M4-40 -> ACT-200 48V/M6-50")
    p = c.project(pid)
    ch = c.call(
        "engineer",
        "POST",
        f"/projects/{pid}/changes",
        json={"base_revision_number": p["current_revision_number"], **SCENARIO["ec001"]},
    )["data"]
    impact = c.call(
        "engineer",
        "POST",
        f"/projects/{pid}/changes/{ch['id']}/publish",
        json={"expected_baseline_version": p["baseline_version"]},
        headers={"Idempotency-Key": f"demo-{ch['id']}"},
    )["data"]
    nodes = {i["node"] for i in impact["impacted"]}
    for n in (
        "SUPPLIER_SELECTION:ACTUATOR_MAIN",
        "COMPATIBILITY_CHECK:MOTOR_VOLTAGE",
        "COMPATIBILITY_CHECK:ACTUATOR_MOUNT",
        "WORK_INSTRUCTION:INSTALL_ACTUATOR",
        "QC_PLAN:MOTOR_FUNCTION_TEST",
        "READINESS_APPROVAL:PROJECT",
    ):
        expect(n in nodes, f"impact includes {n}")
    expect(c.project(pid)["status"] == "BLOCKED", "project BLOCKED after R2")
    stale = c.call(
        a1["assigned_approver_email"].split("@")[0],
        "POST",
        f"/projects/{pid}/approvals/{a1['id']}/decision",
        expect_status=409,
        json={"decision": "APPROVE", "reason": "try stale"},
    )
    expect(stale["code"] == "APPROVAL_STALE", "stale R1 approval refused (409)")
    expect(
        c.call(
            "customer",
            "POST",
            f"/projects/{pid}/approvals/{a1['id']}/decision",
            expect_status=403,
            json={"decision": "APPROVE", "reason": "customer"},
        )["status"]
        == 403,
        "customer approval refused (403)",
    )

    print("5. EC-002 (R3) and evidence-backed resolution of every exception")
    p = c.project(pid)
    ch2 = c.call(
        "engineer",
        "POST",
        f"/projects/{pid}/changes",
        json={"base_revision_number": p["current_revision_number"], **SCENARIO["ec002"]},
    )["data"]
    c.call("engineer", "POST", f"/projects/{pid}/changes/{ch2['id']}/publish", json={})
    for res in SCENARIO["resolutions"]:
        content = json.loads(json.dumps(res["content"]))
        if pin := res.get("pin_artifact_revision_of"):
            arts = c.call("engineer", "GET", f"/projects/{pid}/artifacts", params={"node_key": pin})["data"]
            content["artifact_refs"][pin] = arts[0]["revision"]
        exc = [
            e
            for e in c.call("engineer", "GET", f"/projects/{pid}/exceptions", params={"status": "OPEN"})["data"]
            if e["node_key"] == res["node"]
        ]
        if not exc:
            continue
        c.call(
            "engineer",
            "POST",
            f"/projects/{pid}/exceptions/{exc[0]['id']}/resolve",
            json={"resolution_note": res["note"], "artifact_content": content},
        )
        print(f"  ok  resolved {res['node']}")
    left = c.call("engineer", "GET", f"/projects/{pid}/exceptions", params={"status": "OPEN"})["data"]
    expect(left == [], f"no open exceptions ({len(left)} left)")

    print("6. re-approve the new baseline")
    approve_current(c, pid)
    p = c.project(pid)
    expect(p["status"] == "READY" and p["current_revision_number"] == 3, "R3 READY with fresh approval")
    audit = c.call("admin", "GET", f"/projects/{pid}/audit", params={"page_size": 1})
    expect(audit["meta"]["total"] > 40, f"audit trail recorded ({audit['meta']['total']} events)")
    return pid


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--credentials")
    ap.add_argument("--source", choices=["pdf", "csv"], default="pdf")
    ap.add_argument("--smoke", action="store_true", help="read-only health/auth checks only")
    args = ap.parse_args()
    try:
        smoke(args.base_url)
        if not args.smoke:
            pid = flagship(Client(args.base_url, load_creds(args.credentials)), args.source)
            print(f"\nDEMO PASSED - project {pid}")
    except Fail as e:
        print(f"\nFAILED: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
