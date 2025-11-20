# -*- coding: utf-8 -*-
"""
tasks.py - Stage 1 (L2/L3, создание устройств) + Stage 2 (авторизация в Instagram).
"""

import os
import subprocess
import time
import secrets
import json
import random
from pathlib import Path
from typing import Optional, List, Dict, Any

from .celery_app import celery_app
from .logbus import emit
from .profiles.generator import generate_profile
from .android_tweaker import (
    apply_profile,
    wait_for_boot,
    _adb_get_prop,
    _adb_get_android_id,
)
from .l3.pipeline import run_pipeline
from .instagram_login import login_instagram

BASE_VM = os.environ.get("BASE_VM", "AndroidBase9")

BASE_DIR = Path(__file__).resolve().parent
CATALOG_PATH = BASE_DIR / "profiles" / "catalog.json"
DATA_DIR = BASE_DIR.parent / "data"
DEVICES_DIR = DATA_DIR / "devices"


def _run_cmd(cmd: str, job_id: str, level: str = "DEBUG") -> subprocess.CompletedProcess:
    """Безопасный запуск VBoxManage/ADB с логированием."""
    emit(job_id, level, f"CMD: {cmd}")
    return subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )


@celery_app.task(name="app.tasks.create_devices")
def create_devices(
    job_id: str,
    count: int,
    model_key: str,
    adb_start: int,
    apk_path: Optional[str],
    use_random_model: bool = False,
) -> None:
    """Stage 1: создание клонов и запуск L2/L3."""
    emit(
        job_id,
        "INFO",
        f"Задача {job_id} принята. План: {count} клон(ов), профиль: {model_key}, старт ADB: {adb_start}.",
    )

    # Если выбран режим случайной модели - читаем catalog.json
    all_model_keys: Optional[List[str]] = None
    if use_random_model:
        try:
            with open(CATALOG_PATH, "r", encoding="utf-8") as f:
                catalog_data = json.load(f)
            keys: List[str] = []
            for p in catalog_data:
                k = p.get("key")
                if not k or k == "__random__":
                    continue
                keys.append(k)
            if not keys:
                emit(job_id, "ERROR", "use_random_model=True, но нет валидных моделей в catalog.json.")
                use_random_model = False
            else:
                all_model_keys = keys
        except Exception as e:
            emit(job_id, "ERROR", f"use_random_model=True, но не удалось прочитать catalog.json: {e}")
            use_random_model = False

    devices_info: List[Dict[str, str]] = []

    for i in range(count):
        idx = i + 1
        clone_label = f"Clone_{idx}"

        if use_random_model and all_model_keys:
            chosen_key = random.choice(all_model_keys)
        else:
            chosen_key = model_key

        vm_name = f"{BASE_VM}_clone_{job_id}_{idx}"
        adb_port = adb_start + i
        serial = f"127.0.0.1:{adb_port}"

        emit(
            job_id,
            "INFO",
            f"[{clone_label}] Начинаю работу над {vm_name} (ADB {serial}), модель: {chosen_key}.",
        )

        # 1. Клонирование
        emit(job_id, "INFO", f"[{clone_label}] Клонирование из {BASE_VM}...")
        p = _run_cmd(f'VBoxManage clonevm "{BASE_VM}" --name "{vm_name}" --register', job_id)
        if p.returncode != 0:
            emit(job_id, "ERROR", f"[{clone_label}] Ошибка клонирования. {p.stderr}")
            continue

        # 2. NAT/ADB
        nat_rule_name = f"adb-{adb_port}"
        _run_cmd(f'VBoxManage modifyvm "{vm_name}" --natpf1 delete "{nat_rule_name}"', job_id)
        p = _run_cmd(
            f'VBoxManage modifyvm "{vm_name}" --natpf1 "{nat_rule_name},tcp,,{adb_port},,5555"',
            job_id,
        )
        if p.returncode != 0:
            emit(job_id, "ERROR", f"[{clone_label}] Ошибка проброса NAT. {p.stderr}")
            continue

        # 3. Запуск ВМ
        emit(job_id, "INFO", f"[{clone_label}] Запускаю виртуальную машину (GUI)...")
        p = _run_cmd(f'VBoxManage startvm "{vm_name}" --type gui', job_id, "INFO")
        if p.returncode != 0:
            emit(job_id, "ERROR", f"[{clone_label}] Ошибка запуска ВМ. {p.stderr}")
            continue

        # 4. Подключение ADB и ожидание загрузки
        emit(job_id, "INFO", f"[{clone_label}] Подключаюсь к ADB ({serial})...")
        time.sleep(5)
        _run_cmd(f"adb connect {serial}", job_id)
        if not wait_for_boot(serial, job_id, timeout=300):
            emit(job_id, "ERROR", f"[{clone_label}] ВМ не загрузилась, пропускаю.")
            continue

        devices_info.append({"clone": clone_label, "serial": serial})

        # 5. Генерация L2-профиля
        profile = generate_profile(chosen_key)
        if "ANDROID_ID" not in profile:
            profile["ANDROID_ID"] = secrets.token_hex(8)

        # 6. Применение L2
        apply_profile(serial, profile, job_id, idx)

        # 7. Установка APK
        apk_status = "Не устанавливался"
        if apk_path and os.path.exists(apk_path):
            apk_filename = os.path.basename(apk_path)
            emit(job_id, "INFO", f"[{clone_label}] Устанавливаю APK: {apk_filename}...")
            p = _run_cmd(f'adb -s {serial} install -r -d "{apk_path}"', job_id, "INFO")
            if p.returncode == 0:
                apk_status = f"APK {apk_filename} успешно установлен."
                emit(job_id, "SUCCESS", f"[{clone_label}] {apk_status}")
            else:
                apk_status = f"Ошибка установки APK ({p.stderr})"
                emit(job_id, "ERROR", f"[{clone_label}] {apk_status}")

        # 8. L3-пайплайн
        l3_result: Optional[Dict[str, str]] = None
        try:
            def _l3_log(msg: str, vm_name_local: str = vm_name, clone_label_local: str = clone_label) -> None:
                emit(job_id, "INFO", f"[{clone_label_local}] {msg}", vm_name_local)

            l3_result = run_pipeline(serial, chosen_key, "", _l3_log)
        except Exception as e:
            emit(job_id, "ERROR", f"[{clone_label}] Ошибка L3-пайплайна: {e}", vm_name)

        # 9. Финальная сводка
        final_model = _adb_get_prop(serial, "ro.product.model")
        final_device = _adb_get_prop(serial, "ro.product.device")
        final_release = _adb_get_prop(serial, "ro.build.version.release")
        final_sdk = _adb_get_prop(serial, "ro.build.version.sdk")
        final_android_id = _adb_get_android_id(serial)

        emit(job_id, "SUCCESS", f"--- ФИНАЛЬНАЯ СВОДКА ({clone_label}) ---")
        emit(job_id, "SUCCESS", f"Имя клона: {clone_label} ({vm_name})")
        emit(job_id, "SUCCESS", f"Статус APK: {apk_status}")
        emit(job_id, "SUCCESS", f"Модель: {final_model} ({final_device})")
        emit(job_id, "SUCCESS", f"Версия Android: {final_release} (SDK {final_sdk})")
        emit(job_id, "SUCCESS", f"ANDROID_ID: {final_android_id}")
        if l3_result is not None:
            l3_fp = l3_result.get("fingerprint", "")
            l3_tags = l3_result.get("tags", "")
            l3_type = l3_result.get("type", "")
            emit(job_id, "SUCCESS", f"L3: fingerprint='{l3_fp}', tags='{l3_tags}', type='{l3_type}'")
        else:
            emit(job_id, "SUCCESS", "L3: fingerprint='N/A', tags='N/A', type='N/A'")
        emit(job_id, "SUCCESS", "-------------------------------------")

    # Сохраняем список устройств для Stage 2
    try:
        DEVICES_DIR.mkdir(parents=True, exist_ok=True)
        devices_path = DEVICES_DIR / f"{job_id}.json"
        with devices_path.open("w", encoding="utf-8") as f:
            json.dump(devices_info, f, ensure_ascii=False, indent=2)
        emit(job_id, "INFO", f"[AUTH] Список устройств ({len(devices_info)}) сохранён в {devices_path}")
    except Exception as e:
        emit(job_id, "ERROR", f"[AUTH] Не удалось сохранить список устройств: {e}")

    emit(job_id, "INFO", f"Задача {job_id} завершена.")


@celery_app.task(name="app.tasks.auth_accounts_task")
def auth_accounts_task(job_id: str, accounts_path: str, devices: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Stage 2: авторизация в Instagram по схеме one_to_one."""
    emit(job_id, "INFO", "[AUTH] Задача авторизации запущена.")
    emit(job_id, "INFO", f"[AUTH] Файл аккаунтов: {accounts_path}")
    emit(job_id, "INFO", f"[AUTH] Количество устройств: {len(devices)}")

    accounts: List[Dict[str, Optional[str]]] = []
    try:
        with open(accounts_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                parts = line.split("|")
                if len(parts) < 2:
                    emit(job_id, "WARNING", f"[AUTH] Пропускаю строку без login/password: '{line}'")
                    continue
                login = parts[0].strip()
                password = parts[1].strip()
                totp_secret = parts[2].strip() if len(parts) >= 3 and parts[2].strip() else ""
                accounts.append({"login": login, "password": password, "totp_secret": totp_secret})
        emit(job_id, "INFO", f"[AUTH] Найдено {len(accounts)} валидных аккаунтов.")
    except Exception as e:
        emit(job_id, "ERROR", f"[AUTH] Ошибка чтения файла аккаунтов: {e}")
        emit(job_id, "INFO", "__DONE__")
        return {"status": "error", "reason": str(e), "results": []}

    results: List[Dict[str, Any]] = []

    for idx, dev in enumerate(devices):
        clone_label = dev.get("clone") or f"Device_{idx+1}"
        serial = dev.get("serial")

        if not serial:
            emit(job_id, "ERROR", f"[AUTH][{clone_label}] SKIPPED: отсутствует serial.")
            results.append(
                {"clone": clone_label, "serial": serial, "login": None, "status": "SKIPPED", "reason": "no serial"}
            )
            continue

        if idx >= len(accounts):
            emit(job_id, "WARNING", f"[AUTH][{clone_label}] SKIPPED: no account (index={idx}).")
            results.append(
                {"clone": clone_label, "serial": serial, "login": None, "status": "SKIPPED", "reason": "no account"}
            )
            continue

        acc = accounts[idx]
        login = acc["login"]
        password = acc["password"]
        totp_secret = acc["totp_secret"] or ""
        masked_login = (login[:2] + "***") if login else "<empty>"

        emit(
            job_id,
            "INFO",
            f"[AUTH][{clone_label}] Начинаю авторизацию для login={masked_login} (serial={serial})",
        )

        def _log(msg: str, clone_label_local: str = clone_label) -> None:
            emit(job_id, "INFO", f"[AUTH][{clone_label_local}] {msg}", clone_label_local)

        status = "FAILED"
        reason = ""

        try:
            login_instagram(serial, login, password, totp_secret, _log)
            status = "OK"
            reason = ""
            _log(f"[IG] SUCCESS: login completed for {masked_login}")
        except Exception as e:
            status = "FAILED"
            reason = str(e)
            _log(f"[IG] ERROR: login failed for {masked_login}: {e}")

        results.append(
            {
                "clone": clone_label,
                "serial": serial,
                "login": login,
                "status": status,
                "reason": reason,
            }
        )

    emit(job_id, "INFO", "--- AUTH SUMMARY ---")
    if not devices:
        emit(job_id, "INFO", "Нет устройств (devices=[]).")
    else:
        for r in results:
            clone_label = r.get("clone") or "<unknown>"
            login = r.get("login") or "-"
            masked_login = (login[:2] + "***") if login not in (None, "-", "") else login
            status = r.get("status", "UNKNOWN")
            reason = r.get("reason", "")
            if reason:
                emit(job_id, "INFO", f"{clone_label}: {status} (login={masked_login}, reason={reason})")
            else:
                emit(job_id, "INFO", f"{clone_label}: {status} (login={masked_login})")
    emit(job_id, "INFO", "--------------------")
    emit(job_id, "INFO", "__DONE__")

    return {"status": "ok", "results": results}
