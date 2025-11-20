# -*- coding: utf-8 -*-
"""
android_tweaker.py — L2-совместимая версия для твоего рабочего L1 проекта.

Что сделано (по твоему промту):
1.  Перенесены helper-функции (_adb_get_prop и т.д.) из tasks.py.
2.  Добавлен "белый список" SAFE_RO_KEYS для L2.
3.  apply_profile научился применять L2-поля.
4.  Добавлена верификация "ДО" и "ПОСЛЕ" прямо в apply_profile.
5.  Сохранены все твои "КРИТИЧЕСКИЕ ПРАВИЛА" (безопасный push, remount, text=True).
"""

import os
import shlex
import subprocess
import tempfile
import time
from typing import Dict, Tuple

from .logbus import emit

# -------------------------------------------------------------------
# ЗАДАЧА 1: "Белый список" L2-полей.
# -------------------------------------------------------------------
# Мы будем применять ТОЛЬКО эти поля из catalog.json.
# НИКАКИХ ro.build.fingerprint или ro.hardware.
SAFE_RO_KEYS: Tuple[str, ...] = (
    "ro.product.model",
    "ro.product.brand",
    "ro.product.manufacturer",
    "ro.serialno",
    # L2 Поля
    "ro.product.device",
    "ro.product.name",
    "ro.build.version.release",
    "ro.build.version.sdk",
)

# -------------------------------------------------------------------
# Helper-функции (перенесены из tasks.py для исправления ImportError)
# -------------------------------------------------------------------

def _run_cmd(cmd_list: list, job_id: str = None, level: str = "DEBUG") -> subprocess.CompletedProcess:
    """
    Безопасный запуск subprocess, использует text=True (нет .decode() ошибок).
    """
    if job_id:
        # Логируем только если есть job_id (т.е. это не вызов из apply_profile)
        emit(job_id, level, f"CMD: {' '.join(cmd_list)}")
        
    return subprocess.run(
        cmd_list,
        capture_output=True,
        text=True,  # КРИТИЧЕСКОЕ ПРАВИЛО 5: НИКАКИХ .decode()
        encoding="utf-8",
        errors="ignore"
    )

def _adb(serial: str, *args, timeout: int = 60, check: bool = False) -> subprocess.CompletedProcess:
    """Обертка для ADB команд."""
    cmd_list = ["adb", "-s", serial, *args]
    # В adb мы не можем использовать text=True из-за таймаутов, но мы
    # будем использовать .decode() БЕЗОПАСНО с errors='ignore'
    return subprocess.run(
        cmd_list,
        capture_output=True,
        timeout=timeout,
        check=check,
        encoding="utf-8",
        errors="ignore"
    )

def _adb_get_prop(serial: str, key: str) -> str:
    """Безопасно читает свойство (prop) с устройства."""
    try:
        # Используем subprocess.check_output для простоты
        out = subprocess.check_output(
            ["adb", "-s", serial, "shell", "getprop", key],
            stderr=subprocess.DEVNULL,
            timeout=10,
            text=True, # КРИТИЧЕСКОЕ ПРАВИЛО 5
            encoding="utf-8",
            errors="ignore"
        ).strip()
        return out
    except Exception:
        return ""

def _adb_get_android_id(serial: str) -> str:
    """Безопасно читает ANDROID_ID с устройства."""
    try:
        out = subprocess.check_output(
            ["adb", "-s", serial, "shell", "settings", "get", "secure", "android_id"],
            stderr=subprocess.DEVNULL,
            timeout=10,
            text=True, # КРИТИЧЕСКОЕ ПРАВИЛО 5
            encoding="utf-8",
            errors="ignore"
        ).strip()
        return out
    except Exception:
        return ""

def wait_for_boot(serial: str, job_id: str, timeout: int = 600) -> bool:
    """
    Терпеливо ждет, пока ВМ полностью загрузится (sys.boot_completed = 1).
    """
    emit(job_id, "INFO", f"[{serial}] Ожидаю загрузку Android (sys.boot_completed=1)...")
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            prop = _adb_get_prop(serial, "sys.boot_completed")
            if prop == "1":
                emit(job_id, "SUCCESS", f"[{serial}] Устройство в онлайне.")
                return True
        except Exception:
            pass # Игнорируем ошибки, пока ВМ грузится
        time.sleep(3)
    
    emit(job_id, "ERROR", f"[{serial}] Таймаут ожидания загрузки (sys.boot_completed).")
    return False

# -------------------------------------------------------------------
# ГЛАВНАЯ L2-ФУНКЦИЯ
# -------------------------------------------------------------------

def apply_profile(serial: str, profile: dict, job_id: str, idx: int = 1):
    """
    Применяет L2-профиль, используя все КРИТИЧЕСКИЕ ПРАВИЛА.
    """
    
    label = profile.get("label", "N/A")
    emit(job_id, "INFO", f"[{serial}] Профиль «{label}» выбран. Начинаю применение...")

    # --- ЗАДАЧА 2: Верификация "ДО" ---
    old_model = _adb_get_prop(serial, "ro.product.model")
    old_version = _adb_get_prop(serial, "ro.build.version.release")
    old_android_id = _adb_get_android_id(serial)
    emit(job_id, "DEBUG", f"[{serial}] Текущее состояние ДО... Модель: {old_model} | Версия: {old_version} | ANDROID_ID: {old_android_id}")

    # 1. Получаем Root и Remount
    emit(job_id, "INFO", f"[{serial}] Получаю root-доступ и открываю /system для записи...")
    _adb(serial, "root", timeout=10)
    time.sleep(2) # Даем adbd перезапуститься
    
    # КРИТИЧЕСКОЕ ПРАВИЛО 6: Надежный remount
    remount_cmd = "mount -o rw,remount /system || mount -o rw,remount /"
    p = _adb(serial, "shell", remount_cmd, timeout=10)
    if p.returncode != 0:
        emit(job_id, "WARN", f"[{serial}] Команда remount не удалась. Изменения build.prop могут не примениться.")

    # 2. Применяем "безопасные" L2-поля
    emit(job_id, "INFO", f"[{serial}] Применяю безопасные свойства ro.* (L2)...")
    
    # Собираем все L2-поля в одну строку
    props_to_write = []
    for key in SAFE_RO_KEYS:
        if key in profile:
            props_to_write.append(f"{key}={profile[key]}")

    # КРИТИЧЕСКОЕ ПРАВИЛО 4: Безопасный adb push через tempfile
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write("\n".join(props_to_write))
        f.write("\n")
        temp_file_path = f.name

    try:
        remote_path = "/data/local/tmp/new_props.txt"
        p = _adb(serial, "push", temp_file_path, remote_path, timeout=10)
        if p.returncode != 0:
            emit(job_id, "ERROR", f"[{serial}] Ошибка adb push: {p.stdout} {p.stderr}")
            return
            
        # Дописываем наши новые пропсы в build.prop
        # Этот метод (cat >>) безопасен и не перезаписывает файл, а добавляет в конец.
        # Android читает последнее значение, если ключи дублируются.
        p = _adb(serial, "shell", f"cat {remote_path} >> /system/build.prop", timeout=10)
        if p.returncode != 0:
            emit(job_id, "ERROR", f"[{serial}] Ошибка записи в build.prop: {p.stdout} {p.stderr}")
            
    finally:
        os.remove(temp_file_path) # Чистим за собой

    # 3. Применяем ANDROID_ID
    new_android_id = profile.get("generated_android_id") # Мы его сгенерируем в tasks.py
    if new_android_id:
        emit(job_id, "INFO", f"[{serial}] ANDROID_ID -> {new_android_id}")
        _adb(serial, "shell", "settings", "put", "secure", "android_id", new_android_id, timeout=10)

    # 4. Применяем Экран
    if "wm_size" in profile and "wm_density" in profile:
        size = profile['wm_size']
        density = profile['wm_density']
        emit(job_id, "INFO", f"[{serial}] Экран -> {size} @ {density}dpi")
        _adb(serial, "shell", "wm", "size", size, timeout=10)
        _adb(serial, "shell", "wm", "density", str(density), timeout=10)

    # 5. Перезагрузка
    emit(job_id, "INFO", f"[{serial}] Перезагрузка устройства...")
    # Неблокирующий вызов (fire-and-forget)
    try:
        _adb(serial, "reboot", timeout=5)
    except subprocess.TimeoutExpired:
        pass # Это нормально, устройство обрывает связь
    
    # 6. Ждем, пока ВМ вернется
    emit(job_id, "INFO", f"[{serial}] Ожидание загрузки после перезагрузки...")
    time.sleep(10) # Даем время на начало перезагрузки
    _adb(serial, "wait-for-device", timeout=300)
    wait_for_boot(serial, job_id, timeout=300) # Ждем полного запуска

    # --- ЗАДАЧА 2: Верификация "ПОСЛЕ" ---
    new_model = _adb_get_prop(serial, "ro.product.model")
    new_version = _adb_get_prop(serial, "ro.build.version.release")
    new_sdk = _adb_get_prop(serial, "ro.build.version.sdk")
    new_android_id_check = _adb_get_android_id(serial)

    emit(job_id, "SUCCESS", f"[{serial}] Применено (L2): Модель={new_model} | Версия={new_version} (SDK {new_sdk}) | ANDROID_ID={new_android_id_check}")