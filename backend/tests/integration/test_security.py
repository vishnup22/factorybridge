"""Authentication, authorization, tenancy, input limits, AI-injection resistance, production guards."""

import time
import uuid

import jwt
import pytest
from pydantic import ValidationError

from app.core.config import _INSECURE_DEV_SECRET, Settings, get_settings
from app.core.db import session_factory
from app.core.security import hash_password
from app.models import Organization, User
from tests.integration.conftest import PASSWORD


def _other_org_user(role: str = "engineer") -> str:
    with session_factory()() as db:
        org = Organization(name="Other Synthetic Co", slug=f"other-{uuid.uuid4().hex[:6]}")
        db.add(org)
        db.flush()
        email = f"intruder-{uuid.uuid4().hex[:6]}@other.example"
        db.add(User(organization_id=org.id, email=email, role=role, password_hash=hash_password(PASSWORD)))
        db.commit()
    return email.split("@")[0]


def _login_as(api, local: str, domain: str) -> dict[str, str]:
    r = api.client.post("/api/v1/auth/login", json={"email": f"{local}@{domain}", "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ---------------- authentication ----------------
def test_unauthenticated_and_bad_tokens_rejected(api):
    c = api.client
    assert c.get("/api/v1/projects").status_code == 401
    assert c.get("/api/v1/projects", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401
    s = get_settings()
    me = api.get("engineer", "/auth/me").json()["user"]
    org = api.get("engineer", "/auth/me").json()["organization"]["id"]
    expired = jwt.encode(
        {"sub": me["id"], "org": org, "exp": int(time.time()) - 10, "iat": int(time.time()) - 100},
        s.jwt_secret,
        algorithm="HS256",
    )
    assert c.get("/api/v1/projects", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    forged = jwt.encode({"sub": me["id"], "org": org, "exp": int(time.time()) + 600}, "x" * 40, algorithm="HS256")
    assert c.get("/api/v1/projects", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    none_alg = jwt.encode({"sub": me["id"], "org": org, "exp": int(time.time()) + 600}, key=None, algorithm="none")
    assert c.get("/api/v1/projects", headers={"Authorization": f"Bearer {none_alg}"}).status_code == 401


def test_login_failures_are_generic_and_audited(api):
    bad = api.client.post("/api/v1/auth/login", json={"email": "engineer@demo.example", "password": "wrong-password"})
    unknown = api.client.post("/api/v1/auth/login", json={"email": "ghost@demo.example", "password": "wrong-password"})
    assert bad.status_code == unknown.status_code == 401
    assert bad.json()["detail"] == unknown.json()["detail"]
    events = api.get("admin", "/admin/audit").json()["data"]
    assert any(e["action"] == "LOGIN_FAILED" for e in events)
    assert all("wrong-password" not in str(e) for e in events)


def test_login_rate_limited(api, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_rate_limit_per_minute", 3)
    codes = [
        api.client.post("/api/v1/auth/login", json={"email": "x@demo.example", "password": "nope"}).status_code
        for _ in range(8)
    ]
    assert 429 in codes
    r = api.client.post("/api/v1/auth/login", json={"email": "x@demo.example", "password": "nope"})
    assert r.status_code == 429 and r.headers["content-type"].startswith("application/problem+json")


def test_login_rate_limit_cannot_be_evaded_by_spoofing_forwarded_for(api, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_rate_limit_per_minute", 3)
    monkeypatch.setattr(get_settings(), "trusted_proxy_hops", 1)
    target = f"victim-{uuid.uuid4().hex[:8]}@demo.example"
    codes = [
        api.client.post(
            "/api/v1/auth/login",
            json={"email": target, "password": "guess"},
            headers={"X-Forwarded-For": f"10.9.{i}.1, 203.0.113.{i}"},
        ).status_code
        for i in range(6)
    ]
    # Each request comes from a different proxy-reported IP, so only the per-account bucket can stop it.
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429, 429]


def test_client_ip_uses_only_proxy_appended_forwarded_entry(monkeypatch):
    from starlette.requests import Request

    from app.api.deps import client_ip

    def req(xff: str | None) -> Request:
        headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
        return Request({"type": "http", "headers": headers, "client": ("10.0.0.5", 1234)})

    monkeypatch.setattr(get_settings(), "trusted_proxy_hops", 0)
    assert client_ip(req("1.2.3.4")) == "10.0.0.5"
    monkeypatch.setattr(get_settings(), "trusted_proxy_hops", 1)
    assert client_ip(req("6.6.6.6, 198.51.100.7")) == "198.51.100.7"
    assert client_ip(req(None)) == "10.0.0.5"


def test_deactivated_user_token_stops_working_immediately(api):
    h = api.h("engineer2")
    assert api.client.get("/api/v1/projects", headers=h).status_code == 200
    users = {u["email"]: u["id"] for u in api.get("admin", "/admin/users").json()["data"]}
    r = api.client.patch(
        f"/api/v1/admin/users/{users['engineer2@demo.example']}", json={"is_active": False}, headers=api.h("admin")
    )
    assert r.status_code == 200
    assert api.client.get("/api/v1/projects", headers=h).status_code == 401


def test_role_is_read_from_database_not_token(api):
    pid = api.r1_with_plan()
    a = api.request_approval(pid).json()["data"]
    assignee = api.assignee(pid, a["id"])
    h = api.h(assignee)
    users = {u["email"]: u["id"] for u in api.get("admin", "/admin/users").json()["data"]}
    api.client.patch(
        f"/api/v1/admin/users/{users[assignee + '@demo.example']}", json={"role": "customer"}, headers=api.h("admin")
    )
    r = api.client.post(
        f"/api/v1/projects/{pid}/approvals/{a['id']}/decision",
        headers=h,
        json={"decision": "APPROVE", "reason": "old token"},
    )
    assert r.status_code == 403


def test_admin_cannot_self_escalate_or_lock_out(api):
    me = api.get("admin", "/auth/me").json()["user"]
    r = api.client.patch(f"/api/v1/admin/users/{me['id']}", json={"is_active": False}, headers=api.h("admin"))
    assert r.status_code in (403, 422)
    assert api.client.get("/api/v1/admin/users", headers=api.h("engineer")).status_code == 403


def test_request_body_cannot_smuggle_role_or_status(api):
    pid = api.create_project()
    r = api.post(
        "customer",
        f"/projects/{pid}/approvals/request",
        {"expected_baseline_version": 1, "role": "approver", "status": "APPROVED"},
    )
    assert r.status_code == 403
    r = api.post("customer", "/projects", {"name": "X project", "sku": "X-1", "target_quantity": 1, "status": "READY"})
    assert r.status_code in (201, 422)
    if r.status_code == 201:
        assert r.json()["data"]["status"] == "DRAFT"


# ---------------- tenancy (IDOR) ----------------
def test_cross_org_access_is_not_found(api):
    pid, aid = api.ready_r1()
    doc_id = api.get("engineer", f"/projects/{pid}/documents").json()["data"][0]["id"]
    h = _login_as(api, _other_org_user("engineer"), "other.example")
    for path in (
        f"/projects/{pid}",
        f"/projects/{pid}/bom/revisions",
        f"/projects/{pid}/exceptions",
        f"/projects/{pid}/audit",
        f"/documents/{doc_id}",
        f"/documents/{doc_id}/download",
        f"/documents/{doc_id}/extraction",
    ):
        assert api.client.get(f"/api/v1{path}", headers=h).status_code == 404, path
    assert api.client.get("/api/v1/projects", headers=h).json()["data"] == []
    ha = _login_as(api, _other_org_user("approver"), "other.example")
    r = api.client.post(
        f"/api/v1/projects/{pid}/approvals/{aid}/decision",
        headers=ha,
        json={"decision": "REJECT", "reason": "cross tenant"},
    )
    assert r.status_code == 404
    assert api.project(pid)["status"] == "READY"


def test_customer_view_is_restricted(api):
    pid, _ = api.ready_r1()
    doc_id = api.get("customer", f"/projects/{pid}/documents").json()["data"][0]["id"]
    assert api.get("customer", f"/documents/{doc_id}/extraction").status_code == 403
    assert api.get("customer", f"/projects/{pid}/audit").status_code == 403
    assert api.get("customer", "/admin/users").status_code == 403
    p = api.project(pid, who="customer")
    assert p["status"] == "READY"


# ---------------- input limits ----------------
def test_oversized_upload_rejected(api, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 1024)
    pid = api.create_project()
    r = api.upload(pid, "big.csv", data=b"line_key,part_number\n" + b"A,B\n" * 1000)
    assert r.status_code == 413 and r.json()["code"] == "FILE_TOO_LARGE"


def test_upload_rate_limited(api, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_rate_limit_per_minute", 2)
    pid = api.create_project()
    codes = [
        api.upload(pid, f"f{i}.csv", data=f"line_key,part_number\nA{i},B\n".encode()).status_code for i in range(5)
    ]
    assert 429 in codes


def test_validation_errors_are_problem_json(api):
    r = api.post("customer", "/projects", {"name": "", "sku": "bad sku!", "target_quantity": -1})
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["code"] == "REQUEST_INVALID" and body["correlation_id"]


def test_download_content_disposition_is_safe(api):
    pid = api.create_project()
    data = b"line_key,part_number,part_revision,description,quantity,unit\nA,P-1,A,d,1,each\n"
    up = api.upload(pid, 'evil"\r\nX-Injected: 1.csv', data=data)
    assert up.status_code == 202, up.text
    doc = up.json()["data"]
    assert "\r" not in doc["filename"] and "\n" not in doc["filename"]
    r = api.client.get(f"/api/v1/documents/{doc['id']}/download", headers=api.h("engineer"))
    assert r.status_code == 200
    assert "X-Injected" not in r.headers
    assert r.headers["content-disposition"].count('"') == 2
    assert r.headers["x-content-type-options"] == "nosniff"


# ---------------- AI boundary ----------------
def test_prompt_injection_document_cannot_change_state(api):
    pid = api.create_project()
    doc = api.upload(pid, "requirements_injection.pdf").json()["data"]
    api.run_worker()
    ex = api.get("engineer", f"/documents/{doc['id']}/extraction").json()["data"]
    assert ex["run"]["status"] == "REVIEW_REQUIRED"
    assert {c["kind"] for c in ex["candidates"]} <= {"BOM_LINE", "REQUIREMENT"}
    assert all("approve" not in str(c["value_json"]).lower() for c in ex["candidates"])
    p = api.project(pid)
    assert p["status"] != "READY" and p["current_revision_number"] is None
    assert api.get("admin", f"/projects/{pid}/approvals").json()["data"] == []
    assert all(
        e["action"] not in ("APPROVAL_DECIDED", "APPROVAL_REQUESTED")
        for e in api.get("admin", f"/projects/{pid}/audit").json()["data"]
    )


# ---------------- operational endpoints ----------------
def test_health_and_security_headers(api):
    live = api.client.get("/api/v1/health/live")
    assert live.status_code == 200
    ready = api.client.get("/api/v1/health/ready")
    assert ready.status_code in (200, 503)
    assert {"database", "redis", "storage"} <= set(ready.json()["checks"])
    for h in ("x-content-type-options", "x-frame-options", "referrer-policy", "x-correlation-id"):
        assert h in live.headers, h
    cid = "abcdef12-3456"
    assert api.client.get("/api/v1/health/live", headers={"X-Correlation-ID": cid}).headers["x-correlation-id"] == cid
    assert (
        api.client.get("/api/v1/health/live", headers={"X-Correlation-ID": "<script>"}).headers["x-correlation-id"]
        != "<script>"
    )


def test_cors_only_allows_configured_origins(api):
    ok = api.client.options(
        "/api/v1/projects", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"}
    )
    bad = api.client.options(
        "/api/v1/projects", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"}
    )
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in bad.headers


# ---------------- AT-14 (local part): production configuration guards ----------------
@pytest.mark.parametrize(
    "overrides",
    [
        {"jwt_secret": _INSECURE_DEV_SECRET},
        {"jwt_secret": "short"},
        {"cors_allowed_origins": "*"},
        {"cors_allowed_origins": "http://app.example.com"},
        {"database_url": "postgresql+psycopg://factorybridge:factorybridge@127.0.0.1:55432/factorybridge"},
        {"storage_backend": "local"},
    ],
)
def test_at14_production_refuses_insecure_configuration(overrides):
    base = {
        "environment": "production",
        "jwt_secret": "p" * 48,
        "cors_allowed_origins": "https://factorybridge.example.com",
        "database_url": "postgresql+psycopg://u:s3cret@db.internal:5432/fb?sslmode=require",
        "storage_backend": "s3",
        "object_storage_bucket": "fb-docs",
    }
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**base, **overrides})
    Settings(_env_file=None, **base)


@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "postgresql+psycopg"])
def test_managed_database_urls_use_psycopg_driver(scheme):
    s = Settings(_env_file=None, database_url=f"{scheme}://u:p@db.internal:5432/fb?sslmode=require")
    assert s.database_url == "postgresql+psycopg://u:p@db.internal:5432/fb?sslmode=require"


def test_demo_seed_never_prints_passwords(api, monkeypatch, capsys, tmp_path):
    from demo import seed

    creds = tmp_path / "creds.txt"
    monkeypatch.setenv("FB_CREDENTIALS_FILE", str(creds))
    monkeypatch.delenv("FB_DEMO_PASSWORD", raising=False)
    monkeypatch.setattr("sys.argv", ["seed", "--reset-passwords"])
    assert seed.main() == 0
    out = capsys.readouterr().out
    generated = [line.split()[1] for line in creds.read_text(encoding="utf-8").splitlines()]
    assert len(generated) == len(seed.DEMO_USERS)
    assert not any(pw in out for pw in generated)

    monkeypatch.setenv("FB_DEMO_PASSWORD", "env-supplied-demo-password-123")
    assert seed.main() == 0
    assert "env-supplied-demo-password-123" not in capsys.readouterr().out


def test_demo_seed_requires_secret_password_in_production(monkeypatch, capsys):
    from demo import seed

    prod = Settings(
        _env_file=None,
        environment="production",
        jwt_secret="p" * 48,
        cors_allowed_origins="https://factorybridge.example.com",
        database_url="postgresql+psycopg://u:s3cret@db.internal:5432/fb",
        storage_backend="s3",
    )
    monkeypatch.setattr(seed, "get_settings", lambda: prod)
    monkeypatch.setattr("sys.argv", ["seed"])
    for value in (None, "too-short"):
        if value is None:
            monkeypatch.delenv("FB_DEMO_PASSWORD", raising=False)
        else:
            monkeypatch.setenv("FB_DEMO_PASSWORD", value)
        assert seed.main() == 2
    assert "FB_DEMO_PASSWORD" in capsys.readouterr().err
