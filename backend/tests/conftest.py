import os
import tempfile

# Settings are read once; pin test configuration before any app import.
os.environ.setdefault(
    "TEST_DATABASE_URL", "postgresql+psycopg://factorybridge:factorybridge@127.0.0.1:55432/factorybridge_test"
)
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ["ENVIRONMENT"] = "test"
os.environ["AI_PROVIDER"] = "fixture"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["LOCAL_STORAGE_PATH"] = tempfile.mkdtemp(prefix="fb-test-storage-")
os.environ["LOGIN_RATE_LIMIT_PER_MINUTE"] = "100000"
os.environ["UPLOAD_RATE_LIMIT_PER_MINUTE"] = "100000"
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/15")
