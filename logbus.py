import os, json, datetime
from redis import Redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

_redis = Redis.from_url(REDIS_URL, decode_responses=True)

def emit(job_id: str, level: str, message: str, vm: str | None = None):
    payload = {
        "ts": datetime.datetime.now().strftime("%H:%M:%S"),
        "level": level,
        "message": message,
    }
    if vm:
        payload["vm"] = vm
    _redis.publish(f"logs:{job_id}", json.dumps(payload))
