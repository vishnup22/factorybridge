"""Private object storage. Keys are server-generated (never user filenames)."""

import re
from functools import lru_cache
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

from app.core.config import get_settings

_KEY_RE = re.compile(r"^[a-f0-9\-]{36}/[a-f0-9\-]{36}/[a-f0-9\-]{36}$")


class Storage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def signed_url(self, key: str, filename: str, ttl_seconds: int = 300) -> str | None: ...
    def healthy(self) -> bool: ...


def content_disposition(filename: str) -> str:
    ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "download"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


def _check_key(key: str) -> str:
    if not _KEY_RE.match(key):
        raise ValueError("invalid storage key")
    return key


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / _check_key(key)).resolve()
        if self.root not in p.parents:
            raise ValueError("path traversal")
        return p

    def put(self, key: str, data: bytes, content_type: str) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def signed_url(self, key: str, filename: str, ttl_seconds: int = 300) -> str | None:
        return None  # local backend streams through the authenticated API instead

    def healthy(self) -> bool:
        return self.root.exists()


class S3Storage:
    def __init__(self) -> None:
        import boto3

        s = get_settings()
        self.bucket = s.object_storage_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=s.object_storage_endpoint,
            aws_access_key_id=s.object_storage_access_key,
            aws_secret_access_key=s.object_storage_secret_key,
            region_name=s.object_storage_region,
        )

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=_check_key(key), Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=_check_key(key))["Body"].read()

    def signed_url(self, key: str, filename: str, ttl_seconds: int = 300) -> str | None:
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": _check_key(key),
                "ResponseContentDisposition": content_disposition(filename),
            },
            ExpiresIn=ttl_seconds,
        )

    def healthy(self) -> bool:
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return True
        except Exception:
            return False


@lru_cache
def get_storage() -> Storage:
    s = get_settings()
    if s.storage_backend == "s3":
        return S3Storage()
    return LocalStorage(s.local_storage_path)
