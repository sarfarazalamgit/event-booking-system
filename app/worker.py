from typing import ClassVar

from arq.connections import RedisSettings

from app.config import settings
from app.jobs import booking_confirmation, event_update_notification


class WorkerSettings:
    functions: ClassVar[list] = [
        booking_confirmation,
        event_update_notification,
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 20
    job_timeout = 60
    max_tries = 5
    keep_result = 3600
