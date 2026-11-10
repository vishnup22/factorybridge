"""S3Storage against a real S3-compatible endpoint (RustFS or MinIO). Skipped unless S3_TEST_ENDPOINT is set."""

import os
import uuid

import httpx
import pytest

from app.core.config import get_settings
from app.services.storage import S3Storage

ENDPOINT = os.environ.get("S3_TEST_ENDPOINT")
pytestmark = pytest.mark.skipif(not ENDPOINT, reason="S3_TEST_ENDPOINT not set")


@pytest.fixture
def s3(monkeypatch) -> S3Storage:
    s = get_settings()
    bucket = f"fb-test-{uuid.uuid4().hex[:12]}"
    monkeypatch.setattr(s, "object_storage_endpoint", ENDPOINT)
    monkeypatch.setattr(s, "object_storage_bucket", bucket)
    monkeypatch.setattr(s, "object_storage_access_key", os.environ["S3_TEST_ACCESS_KEY"])
    monkeypatch.setattr(s, "object_storage_secret_key", os.environ["S3_TEST_SECRET_KEY"])
    monkeypatch.setattr(s, "object_storage_region", "us-east-1")
    storage = S3Storage()
    assert not storage.healthy()
    storage.client.create_bucket(Bucket=bucket)
    return storage


def test_s3_roundtrip_private_objects_and_signed_download(s3: S3Storage):
    key = f"{uuid.uuid4()}/{uuid.uuid4()}/{uuid.uuid4()}"
    s3.put(key, b"%PDF-1.4 synthetic", "application/pdf")
    assert s3.healthy()
    assert s3.get(key) == b"%PDF-1.4 synthetic"

    unsigned = httpx.get(f"{ENDPOINT}/{s3.bucket}/{key}")
    assert unsigned.status_code == 403

    url = s3.signed_url(key, 'evil"\r\nX-Injected: 1.pdf', ttl_seconds=60)
    assert url
    r = httpx.get(url)
    assert r.status_code == 200 and r.content == b"%PDF-1.4 synthetic"
    disposition = r.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="evil_')
    assert "\r" not in disposition and "\n" not in disposition and "x-injected" not in {k.lower() for k in r.headers}


def test_s3_rejects_non_server_generated_keys(s3: S3Storage):
    with pytest.raises(ValueError):
        s3.put("../../etc/passwd", b"x", "text/plain")
