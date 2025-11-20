import os
from celery import Celery

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery("app", broker=REDIS_URL, backend=REDIS_URL)
# don't rely on autodiscover to avoid surprises; tasks import explicitly in tasks.py
