"""Concurrency, worker failure/replay, retries and outbox behaviour (AT-08, AT-09, AT-10 concurrency)."""

import threading
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.core.db import session_factory
from app.models import (
    ApprovalRequest,
    BomRevision,
    Document,
    ExtractionRun,
    OutboxEvent,
    ProcessingJob,
    Project,
    RequirementCandidate,
)
from app.models.base import utcnow
from app.services import outbox, state
from app.services.extraction import pipeline
from app.services.extraction.providers import FixtureProvider, ProviderError
from tests.fixtures import EC001
from tests.integration.conftest import FIXTURES

SENSOR_TWEAK = [{"line_key": "SENSOR", "operation": "UPDATE", "new": {"description": "Synthetic proximity sensor v2"}}]


def parallel(*fns: Callable[[], Any]) -> list[Any]:
    barrier = threading.Barrier(len(fns))
    results: list[Any] = [None] * len(fns)
    errors: list[BaseException] = []

    def run(i: int, fn: Callable[[], Any]) -> None:
        try:
            barrier.wait(timeout=10)
            results[i] = fn()
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=run, args=(i, f)) for i, f in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors, errors
    return results


def fresh_client():
    from app.main import app

    return TestClient(app)


def assert_invariant_i1(pid: str) -> None:
    """READY implies a non-stale APPROVED approval whose hash equals the current baseline hash."""
    with session_factory()() as db:
        p = db.get(Project, uuid.UUID(pid))
        if p.status != "READY":
            return
        a = db.get(ApprovalRequest, p.valid_approval_id) if p.valid_approval_id else None
        assert a is not None and a.status == "APPROVED"
        assert a.baseline_hash == state.current_baseline_hash(db, p)
        assert a.baseline_version == p.baseline_version


# ---------------- AT-08: approve vs edit race ----------------
@pytest.mark.parametrize("iteration", range(6))
def test_at08_approve_publish_race_never_ready_on_stale_baseline(api, iteration):
    pid = api.r1_with_plan()
    a = api.request_approval(pid).json()["data"]
    approver = api.assignee(pid, a["id"])
    cid = api.propose(pid, SENSOR_TWEAK if iteration % 2 else EC001).json()["data"]["id"]
    approve_h, publish_h = api.h(approver), api.h("engineer")

    def approve():
        return fresh_client().post(
            f"/api/v1/projects/{pid}/approvals/{a['id']}/decision",
            headers=approve_h,
            json={"decision": "APPROVE", "reason": "race", "expected_baseline_hash": a["baseline_hash"]},
        )

    def publish():
        return fresh_client().post(f"/api/v1/projects/{pid}/changes/{cid}/publish", headers=publish_h, json={})

    ra, rp = parallel(approve, publish) if iteration < 3 else parallel(publish, approve)[::-1]
    assert rp.status_code == 200, rp.text
    assert ra.status_code in (200, 409), ra.text
    p = api.project(pid)
    assert p["status"] != "READY"
    assert p["current_revision_number"] == 2
    approvals = api.get("admin", f"/projects/{pid}/approvals").json()["data"]
    assert [x["status"] for x in approvals] == ["STALE"]
    assert_invariant_i1(pid)


def test_concurrent_publishes_from_same_base_one_wins(api):
    pid, _ = api.ready_r1()
    c1 = api.propose(pid, EC001).json()["data"]["id"]
    c2 = api.propose(pid, SENSOR_TWEAK).json()["data"]["id"]
    h = api.h("engineer")
    r = parallel(
        *[
            lambda c=c: fresh_client().post(f"/api/v1/projects/{pid}/changes/{c}/publish", headers=h, json={})
            for c in (c1, c2)
        ]
    )
    codes = sorted(x.status_code for x in r)
    assert codes == [200, 409], [x.text for x in r]
    loser = next(x for x in r if x.status_code == 409)
    assert loser.json()["code"] in ("BASE_REVISION_NOT_CURRENT", "STALE_BASE_REVISION")
    with session_factory()() as db:
        n = db.execute(
            select(func.count()).select_from(BomRevision).where(BomRevision.project_id == uuid.UUID(pid))
        ).scalar()
    assert n == 2
    assert_invariant_i1(pid)


def test_concurrent_same_idempotency_key_executes_once(api):
    pid = api.r1_with_plan()
    cid = api.propose(pid, SENSOR_TWEAK).json()["data"]["id"]
    h = api.h("engineer", **{"Idempotency-Key": str(uuid.uuid4())})
    r = parallel(
        *[lambda: fresh_client().post(f"/api/v1/projects/{pid}/changes/{cid}/publish", headers=h, json={})] * 3
    )
    assert [x.status_code for x in r] == [200, 200, 200], [x.text for x in r]
    assert sum(1 for x in r if x.headers.get("Idempotent-Replayed") == "true") == 2
    assert api.project(pid)["current_revision_number"] == 2


def test_concurrent_decisions_on_one_approval(api):
    pid = api.r1_with_plan()
    a = api.request_approval(pid).json()["data"]
    h = api.h(api.assignee(pid, a["id"]))
    r = parallel(
        *[
            lambda d=d: fresh_client().post(
                f"/api/v1/projects/{pid}/approvals/{a['id']}/decision",
                headers=h,
                json={"decision": d, "reason": "concurrent"},
            )
            for d in ("APPROVE", "REJECT")
        ]
    )
    assert sorted(x.status_code for x in r) == [200, 409]
    decided = api.get("admin", f"/projects/{pid}/audit", params={"action": "APPROVAL_DECIDED"}).json()["data"]
    assert len(decided) == 1
    assert_invariant_i1(pid)


# ---------------- AT-09: worker death and replay ----------------
def _upload_job(api, name: str = "requirements_v1.pdf") -> tuple[str, str, uuid.UUID]:
    pid = api.create_project()
    doc = api.upload(pid, name).json()["data"]
    return pid, doc["id"], uuid.UUID(doc["job"]["id"])


def _crash(point: str) -> Callable[[str], None]:
    def hook(p: str) -> None:
        if p == point:
            raise pipeline.SimulatedCrash(p)

    return hook


def _counts(doc_id: str) -> tuple[int, int]:
    with session_factory()() as db:
        runs = db.execute(select(ExtractionRun).where(ExtractionRun.document_id == uuid.UUID(doc_id))).scalars().all()
        cands = db.execute(
            select(func.count())
            .select_from(RequirementCandidate)
            .where(RequirementCandidate.extraction_run_id.in_([r.id for r in runs]))
        ).scalar()
        return len(runs), cands


def _expire_lease(job_id: uuid.UUID) -> None:
    with session_factory()() as db:
        db.execute(
            update(ProcessingJob).where(ProcessingJob.id == job_id).values(started_at=utcnow() - timedelta(hours=1))
        )
        db.commit()


@pytest.mark.parametrize("point", ["after_work", "before_commit"])
def test_at09_worker_death_then_recovery_creates_no_duplicates(api, point):
    pid, doc_id, job_id = _upload_job(api)
    with pytest.raises(pipeline.SimulatedCrash):
        pipeline.process_job(job_id, fault=_crash(point))
    assert _counts(doc_id) == (0, 0)
    with session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        assert job.status == "RUNNING" and job.attempt == 1
        assert job_id not in pipeline.recoverable_jobs(db)
    assert pipeline.process_job(job_id).status == "SKIPPED"  # lease still held

    _expire_lease(job_id)
    with session_factory()() as db:
        assert job_id in pipeline.recoverable_jobs(db)
    assert pipeline.process_job(job_id).status == "DONE"
    assert _counts(doc_id) == (1, 8)

    # replay: direct re-delivery and outbox re-dispatch are both no-ops
    assert pipeline.process_job(job_id).status == "SKIPPED"
    with session_factory()() as db:
        db.execute(update(OutboxEvent).values(published_at=None))
        db.commit()
    api.run_worker()
    assert _counts(doc_id) == (1, 8)
    with session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        assert job.status == "SUCCEEDED" and job.attempt == 2
    assert (
        len(api.get("admin", f"/projects/{pid}/audit", params={"action": "EXTRACTION_COMPLETED"}).json()["data"]) == 1
    )


def test_at09_zombie_worker_cannot_commit_after_lease_takeover(api):
    _, doc_id, job_id = _upload_job(api)
    outcomes: list[str] = []

    def takeover(p: str) -> None:
        if p == "after_work":
            _expire_lease(job_id)
            outcomes.append(pipeline.process_job(job_id).status)

    zombie = pipeline.process_job(job_id, fault=takeover)
    assert outcomes == ["DONE"]
    assert zombie.status == "SKIPPED" and zombie.detail == "lease lost"
    assert _counts(doc_id) == (1, 8)


def test_outbox_concurrent_dispatchers_publish_each_event_once(api):
    pid = api.create_project()
    for i in range(6):
        data = (FIXTURES / "bom_v1.csv").read_text().replace("Synthetic battery pack", f"Synthetic battery pack {i}")
        api.upload(pid, f"bom_{i}.csv", data=data.encode())
    seen: list[str] = []
    lock = threading.Lock()

    def publish(ev: OutboxEvent) -> None:
        with lock:
            seen.append(str(ev.id))

    def dispatch() -> int:
        with session_factory()() as db:
            return outbox.dispatch_pending(db, publish)

    sent = parallel(dispatch, dispatch, dispatch)
    assert sum(sent) == len(seen) == len(set(seen))
    with session_factory()() as db:
        assert (
            db.execute(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.published_at.is_(None))).scalar()
            == 0
        )


def test_outbox_broker_failure_leaves_event_for_next_pass(api):
    api.create_project()
    pid = api.create_project()
    api.upload(pid, "bom_v1.csv")

    def broken(ev: OutboxEvent) -> None:
        raise ConnectionError("broker unavailable")

    with session_factory()() as db:
        assert outbox.dispatch_pending(db, broken) == 0
    with session_factory()() as db:
        ev = db.execute(select(OutboxEvent).where(OutboxEvent.event_type == "DocumentUploaded")).scalar_one()
        assert ev.published_at is None and ev.attempts == 1 and "broker unavailable" in ev.last_error
    assert api.run_worker() >= 1


# ---------------- retries and failure visibility ----------------
class FlakyProvider:
    mode = "FIXTURE"
    model = "flaky"

    def __init__(self, failures: int, exc: BaseException | None = None):
        self.failures = failures
        self.calls = 0
        self.exc = exc

    def extract(self, pages, request_id):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc or ProviderError("PROVIDER_TIMEOUT", "simulated timeout", retryable=True)
        return FixtureProvider().extract(pages, request_id)


def _make_due(job_id: uuid.UUID) -> None:
    with session_factory()() as db:
        db.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job_id)
            .values(next_retry_at=utcnow() - timedelta(minutes=5))
        )
        db.commit()


def test_transient_failures_retry_then_succeed(api):
    _, doc_id, job_id = _upload_job(api)
    prov = FlakyProvider(failures=2)
    first = pipeline.process_job(job_id, provider=prov)
    assert first.status == "RETRY" and first.retry_in_seconds and first.retry_in_seconds >= 2
    assert pipeline.process_job(job_id, provider=prov).status == "SKIPPED"  # not due yet
    with session_factory()() as db:
        d = db.get(Document, uuid.UUID(doc_id))
        assert d.error_code == "PROVIDER_TIMEOUT" and "retrying" in d.error_detail
    _make_due(job_id)
    with session_factory()() as db:
        assert job_id in pipeline.recoverable_jobs(db)
    assert pipeline.process_job(job_id, provider=prov).status == "RETRY"
    _make_due(job_id)
    assert pipeline.process_job(job_id, provider=prov).status == "DONE"
    with session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        assert job.status == "SUCCEEDED" and job.attempt == 3 and job.error_code is None
    assert _counts(doc_id) == (1, 8)


@pytest.mark.parametrize(
    "exc,code",
    [
        (None, "PROVIDER_TIMEOUT"),
        (RuntimeError("boom"), "INTERNAL_ERROR"),
    ],
)
def test_retries_exhausted_fail_visibly(api, exc, code):
    pid, doc_id, job_id = _upload_job(api)
    prov = FlakyProvider(failures=99, exc=exc)
    statuses = []
    for _ in range(3):
        statuses.append(pipeline.process_job(job_id, provider=prov).status)
        _make_due(job_id)
    assert statuses == ["RETRY", "RETRY", "FAILED"]
    assert pipeline.process_job(job_id, provider=prov).status == "SKIPPED"
    d = api.get("engineer", f"/documents/{doc_id}").json()["data"]
    assert d["processing_status"] == "FAILED" and d["error_code"] == code and d["job"]["attempt"] == 3
    ex = api.get("engineer", f"/documents/{doc_id}/extraction").json()["data"]
    assert ex["run"]["status"] == "FAILED" and ex["candidates"] == []
    assert api.project(pid)["current_revision_number"] is None


def test_permanent_provider_error_does_not_retry(api):
    _, doc_id, job_id = _upload_job(api)
    prov = FlakyProvider(failures=99, exc=ProviderError("PROVIDER_REJECTED", "400 bad request", retryable=False))
    assert pipeline.process_job(job_id, provider=prov).status == "FAILED"
    assert prov.calls == 1


def test_tampered_blob_fails_checksum(api):
    from app.services.storage import get_storage

    _, doc_id, job_id = _upload_job(api, "bom_v1.csv")
    with session_factory()() as db:
        key = db.get(Document, uuid.UUID(doc_id)).storage_key
    get_storage().put(key, b"line_key,part_number\nX,Y\n", "text/csv")
    out = pipeline.process_job(job_id)
    assert out.status == "FAILED" and out.detail == "CHECKSUM_MISMATCH"


def test_extraction_conflict_must_be_edited_or_rejected(api):
    pid = api.create_project()
    doc = api.upload(pid, "requirements_conflict.pdf").json()["data"]
    api.run_worker()
    ex = api.get("engineer", f"/documents/{doc['id']}/extraction").json()["data"]
    conflicted = [c for c in ex["candidates"] if c["review_state"] == "CONFLICT"]
    assert len(conflicted) == 2
    opened = api.exceptions(pid)
    assert [e["reason_code"] for e in opened] == ["EXTRACTION_CONFLICT"]
    p = api.project(pid)
    assert p["status"] in ("BLOCKED", "DRAFT", "EXTRACTING", "REVIEW_REQUIRED")
    decisions = [{"candidate_id": c["id"], "action": "ACCEPT"} for c in ex["candidates"]]
    assert (
        api.post(
            "engineer", f"/documents/{doc['id']}/extraction/accept", {"decisions": decisions, "reason": "try"}
        ).status_code
        == 422
    )
    keep, drop = conflicted
    decisions = [
        {"candidate_id": c["id"], "action": "ACCEPT"} for c in ex["candidates"] if c["review_state"] != "CONFLICT"
    ]
    decisions += [
        {
            "candidate_id": keep["id"],
            "action": "EDIT",
            "value": keep["value_json"],
            "note": "Customer confirmed qty 2 (synthetic)",
        },
        {"candidate_id": drop["id"], "action": "REJECT"},
    ]
    r = api.post(
        "engineer", f"/documents/{doc['id']}/extraction/accept", {"decisions": decisions, "reason": "resolved"}
    )
    assert r.status_code == 200, r.text
    assert api.exceptions(pid) == []
    assert api.exceptions(pid, "RESOLVED")[0]["reason_code"] == "EXTRACTION_CONFLICT"
