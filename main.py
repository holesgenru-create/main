import os
import json
import tempfile
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import (
    FastAPI,
    UploadFile,
    File,
    Form,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
)
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from redis.asyncio import Redis

from app.celery_app import celery_app

app = FastAPI()

# CORS (можно упростить/ужесточить по желанию)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent       # app/
PROJECT_ROOT = BASE_DIR.parent                   # корень backend/
STATIC_DIR = PROJECT_ROOT / "static"
DATA_DIR = PROJECT_ROOT / "data"
ACCOUNTS_DIR = DATA_DIR / "accounts"
DEVICES_DIR = DATA_DIR / "devices"
CATALOG_PATH = BASE_DIR / "profiles" / "catalog.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)
ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
DEVICES_DIR.mkdir(parents=True, exist_ok=True)

# Раздача статики /static/*
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def root_redirect():
    # Редиректим на /ui
    return RedirectResponse("/ui", status_code=307)


@app.get("/ui", include_in_schema=False)
def ui():
    # Отдаём наш новый фронт из static/index.html
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        # На всякий случай — fallback
        raise HTTPException(status_code=500, detail="static/index.html not found")
    return FileResponse(index_path)


@app.get("/api/health")
def health():
    return {"ok": True}


def _read_models_from_catalog() -> List[Dict[str, str]]:
    models: List[Dict[str, str]] = []
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # предполагаем список объектов с ключами key/name
    if isinstance(data, list):
        for p in data:
            key = p.get("key")
            if not key:
                continue
            name = p.get("name") or p.get("label") or key
            models.append({"key": key, "name": name})
    elif isinstance(data, dict):
        # запасной вариант, если каталог — dict
        for key, val in data.items():
            if not key:
                continue
            if isinstance(val, dict):
                name = val.get("name", key)
            else:
                name = str(val)
            models.append({"key": key, "name": name})

    if not models:
        raise ValueError("empty catalog")

    return models


# Фоллбек на случай проблем с catalog.json
_FALLBACK_MODELS: List[Dict[str, str]] = [
    {"key": "Google_Pixel_6", "name": "Google Pixel 6"},
    {"key": "Samsung_S21", "name": "Samsung Galaxy S21"},
    {"key": "Xiaomi_Mi9", "name": "Xiaomi Mi 9"},
]


def _safe_models() -> List[Dict[str, str]]:
    try:
        return _read_models_from_catalog()
    except Exception:
        return _FALLBACK_MODELS


def _models_with_random() -> List[Dict[str, str]]:
    base = _safe_models()
    random_entry = {"key": "__random__", "name": "🎲 Random"}

    # убираем возможный дубликат __random__
    keys = {m.get("key") for m in base}
    if "__random__" in keys:
        base = [m for m in base if m.get("key") != "__random__"]

    return [random_entry] + base


@app.get("/api/models")
def get_models():
    # как и договорились: Random первым, потом реальные модели
    return {"models": _models_with_random()}


@app.get("/api/models/")
def get_models_slash():
    # на случай если фронт дергает с / в конце
    return {"models": _models_with_random()}


@app.post("/api/devices/create")
async def api_create(
    count: int = Form(...),
    model_key: str = Form(...),
    adb_start: int = Form(...),
    apk: Optional[UploadFile] = File(None),
):
    """
    Stage 1 — создание устройств.
    Принимает multipart/form-data (FormData с фронта):
      - count
      - model_key (в т.ч. "__random__")
      - adb_start
      - apk (файл .apk, опционально)
    """
    job_id = os.urandom(4).hex()

    # Сохраняем APK во временный файл, если он есть
    tmp_apk_path: Optional[str] = None
    if apk is not None and apk.filename:
        fd, path = tempfile.mkstemp(prefix="upload_", suffix=".apk")
        with os.fdopen(fd, "wb") as f:
            f.write(await apk.read())
        tmp_apk_path = path

    use_random_model = model_key == "__random__"

    # Если Random — подставляем любой валидный ключ как заглушку,
    # но реальный выбор по клонкам делается в таске по флагу use_random_model
    effective_model_key = model_key
    if use_random_model:
        models = _safe_models()
        for m in models:
            k = m.get("key")
            if k and k != "__random__":
                effective_model_key = k
                break

    # Запускаем Celery-таску по имени (см. app/tasks.py)
    celery_app.send_task(
        "app.tasks.create_devices",
        args=[
            job_id,
            int(count),
            effective_model_key,
            int(adb_start),
            tmp_apk_path or None,
            use_random_model,
        ],
    )

    return {"job_id": job_id}


@app.post("/api/auth/upload")
async def api_auth_upload(file: UploadFile = File(...)):
    """
    Загружаем txt-файл с аккаунтами формата login|password|2fa_key,
    сохраняем в data/accounts/acc_<uuid>.txt, возвращаем accounts_id + количество строк.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)

    accounts_id = f"acc_{os.urandom(8).hex()}"
    path = ACCOUNTS_DIR / f"{accounts_id}.txt"

    raw = await file.read()
    text = raw.decode("utf-8", errors="ignore")

    lines_raw = text.splitlines()
    lines = [line.strip() for line in lines_raw if line.strip()]

    # Важно: ВОТ ЗДЕСЬ раньше и была syntax error.
    # Строка должна быть именно в таком виде:
    with path.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return {
        "accounts_id": accounts_id,
        "path": str(path),   # на фронте можно не использовать
        "lines": len(lines),
    }


class AuthStartRequest(BaseModel):
    job_id: str
    accounts_id: str
    mode: str = "one_to_one"


@app.post("/api/auth/start")
async def api_auth_start(payload: AuthStartRequest):
    """
    Запускаем авторизацию (Stage 2).
    Берём:
      - список устройств из data/devices/<job_id>.json,
      - файл аккаунтов из data/accounts/<accounts_id>.txt,
    и передаём их в Celery-таску auth_accounts_task.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    DEVICES_DIR.mkdir(parents=True, exist_ok=True)

    accounts_path = ACCOUNTS_DIR / f"{payload.accounts_id}.txt"
    if not accounts_path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Accounts file not found for accounts_id={payload.accounts_id}",
        )

    devices_path = DEVICES_DIR / f"{payload.job_id}.json"
    if not devices_path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Devices list not found for job_id={payload.job_id}",
        )

    try:
        with devices_path.open("r", encoding="utf-8") as f:
            devices = json.load(f)
        if not isinstance(devices, list):
            raise ValueError("devices JSON is not a list")
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read devices list: {e}",
        )

    if payload.mode != "one_to_one":
        raise HTTPException(status_code=400, detail="Only mode='one_to_one' is supported for now")

    celery_app.send_task(
        "app.tasks.auth_accounts_task",
        args=[payload.job_id, str(accounts_path), devices],
    )

    return {"status": "started", "job_id": payload.job_id}


@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket, job_id: str):
    """
    WebSocket для логов.
    Читает из Redis-канала logs:{job_id} и стримит строки на фронт.
    """
    await ws.accept()
    redis = Redis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )
    pubsub = redis.pubsub()
    channel = f"logs:{job_id}"
    await pubsub.subscribe(channel)
    try:
        # Одно служебное сообщение для фронта
        await ws.send_text(
            json.dumps(
                {
                    "ts": "",
                    "level": "INFO",
                    "message": f"Subscribed logs:{job_id}",
                }
            )
        )
        while True:
            msg = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=1.0,
            )
            if msg:
                await ws.send_text(msg["data"])
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await pubsub.unsubscribe(channel)
        except Exception:
            pass
        await redis.aclose()
