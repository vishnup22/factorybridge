from celery import Celery

from app.core.config import get_settings
from app.observability.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)

celery_app = Celery("factorybridge", broker=settings.redis_url, include=["app.workers.tasks"])
celery_app.conf.update(
    task_acks_late=True,  # ack after completion: a killed worker's message is redelivered
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=300,
    task_soft_time_limit=240,
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 600},
    beat_schedule={
        "dispatch-outbox": {"task": "factorybridge.dispatch_outbox", "schedule": 2.0},
        "recover-jobs": {"task": "factorybridge.recover_jobs", "schedule": 30.0},
    },
    timezone="UTC",
)
