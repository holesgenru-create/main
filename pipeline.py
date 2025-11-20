import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional, Dict, Any

# Путь до L3-каталога профилей
CATALOG_L3_PATH = Path(__file__).resolve().parent.parent / "profiles" / "catalog_l3.json"

# Куда кладём профиль внутри ВМ
REMOTE_PROFILE_PATH = "/data/local/tmp/device_profile.prop"

# Таймауты для ADB
ADB_TIMEOUT = 60
ADB_WAIT_TIMEOUT = 120


def _run_adb(
    serial: str,
    args: list[str],
    log: Callable[[str], None],
    timeout: int = ADB_TIMEOUT,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """
    Вспомогательная функция для запуска ADB-команд.

    ВАЖНО: при успешном выполнении НИЧЕГО не логируем,
    чтобы не засорять вывод. Логируем только ошибки.
    """
    cmd = ["adb", "-s", serial] + args
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
        )
    except subprocess.TimeoutExpired:
        log(f"[L3] ERROR: ADB timeout {timeout}s for: {' '.join(cmd)}")
        raise
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip() if e.stderr else ""
        log(f"[L3] ERROR: ADB failed (exit {e.returncode}) for: {' '.join(cmd)}")
        if stderr:
            log(f"[L3] STDERR: {stderr}")
        raise
    except FileNotFoundError:
        log("[L3] ERROR: 'adb' not found in PATH")
        raise
    except Exception as e:
        log(f"[L3] ERROR: Unexpected ADB error: {e}")
        raise


def _wait_for_device(serial: str, log: Callable[[str], None]) -> None:
    """
    Ждём, пока устройство станет доступно.

    Логов нет (кроме ошибки) — чтобы не спамить.
    """
    _run_adb(serial, ["wait-for-device"], log=log, timeout=ADB_WAIT_TIMEOUT, check=True)


def _push_file(serial: str, local_path: str, remote_path: str, log: Callable[[str], None]) -> None:
    """Отправка файла в ВМ через adb push."""
    _run_adb(serial, ["push", local_path, remote_path], log=log, timeout=ADB_TIMEOUT, check=True)


def _get_prop(serial: str, name: str, log: Callable[[str], None]) -> str:
    """Чтение системного проперти."""
    try:
        result = _run_adb(serial, ["shell", "getprop", name], log=log, timeout=ADB_TIMEOUT, check=True)
        return (result.stdout or "").strip()
    except Exception:
        log(f"[L3] WARN: failed to read prop '{name}'")
        return ""


def _apply_profile_with_resetprop(
    serial: str,
    profile: Dict[str, Any],
    log: Callable[[str], None],
) -> None:
    """
    Применение профиля через resetprop.

    Шаги:
      1) resetprop -f /data/local/tmp/device_profile.prop
      2) resetprop ro.build.fingerprint ...
      3) resetprop ro.build.tags ...
      4) resetprop ro.build.type ...
    """
    # 1. Применяем весь файл
    _run_adb(
        serial,
        ["shell", "su", "0", "/sbin/resetprop", "-f", REMOTE_PROFILE_PATH],
        log=log,
        timeout=ADB_TIMEOUT,
        check=True,
    )

    # 2–4. Явно прописываем три ключевых поля
    fp = profile.get("ro.build.fingerprint")
    tags = profile.get("ro.build.tags")
    typ = profile.get("ro.build.type")

    if fp:
        _run_adb(
            serial,
            ["shell", "su", "0", "/sbin/resetprop", "ro.build.fingerprint", str(fp)],
            log=log,
            timeout=ADB_TIMEOUT,
            check=True,
        )

    if tags:
        _run_adb(
            serial,
            ["shell", "su", "0", "/sbin/resetprop", "ro.build.tags", str(tags)],
            log=log,
            timeout=ADB_TIMEOUT,
            check=True,
        )

    if typ:
        _run_adb(
            serial,
            ["shell", "su", "0", "/sbin/resetprop", "ro.build.type", str(typ)],
            log=log,
            timeout=ADB_TIMEOUT,
            check=True,
        )


def run_pipeline(
    serial: str,
    model_key: str,
    apk_path: str,
    log: Callable[[str], None],
) -> Optional[Dict[str, str]]:
    """
    Минимальный по логам L3-пайплайн.

    Делает:
      1. Ждёт устройство.
      2. Берёт профиль из catalog_l3.json.
      3. Генерит device_profile.prop и пушит в ВМ.
      4. Применяет профиль через resetprop.
      5. Читает итоговые fingerprint/tags/type и возвращает их.

    В лог выводит:
      - только ошибки;
      - один финальный отчёт в виде:
        [REPORT] fingerprint='...', tags='...', type='...'

    Возвращает dict с этими полями или None при ошибке.
    """
    try:
        # 1. Ждём устройство
        _wait_for_device(serial, log)

        # 2. Читаем L3-каталог
        if not CATALOG_L3_PATH.exists():
            log(f"[L3] ERROR: L3 catalog not found at {CATALOG_L3_PATH}")
            return None

        try:
            with open(CATALOG_L3_PATH, "r", encoding="utf-8") as f:
                catalog = json.load(f)
        except Exception as e:
            log(f"[L3] ERROR: failed to read L3 catalog: {e}")
            return None

        profile = catalog.get(model_key)
        if not isinstance(profile, dict):
            log(f"[L3] ERROR: model '{model_key}' not found in L3 catalog.")
            return None

        # 3. Генерим временный prop-файл
        lines: list[str] = []
        for key, value in profile.items():
            if value is None:
                continue
            v = str(value).replace("\n", " ").replace("\r", "")
            lines.append(f"{key}={v}")

        if not lines:
            log(f"[L3] ERROR: empty L3 profile for '{model_key}'")
            return None

        content = "\n".join(lines)

        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile("w", delete=False, suffix=".prop", encoding="utf-8") as tmp:
                tmp.write(content)
                tmp_path = tmp.name

            # Пушим файл в ВМ
            _push_file(serial, tmp_path, REMOTE_PROFILE_PATH, log)

        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        # 4. До/после apply — читаем fingerprint/tags/type
        # (значения "до" можно при желании потом где-то логировать отдельно)
        _before_fp = _get_prop(serial, "ro.build.fingerprint", log)
        _before_tags = _get_prop(serial, "ro.build.tags", log)
        _before_type = _get_prop(serial, "ro.build.type", log)

        # Применяем профиль
        _apply_profile_with_resetprop(serial, profile, log)

        after_fp = _get_prop(serial, "ro.build.fingerprint", log)
        after_tags = _get_prop(serial, "ro.build.tags", log)
        after_type = _get_prop(serial, "ro.build.type", log)

        # Один финальный отчёт — именно его ты будешь видеть в логах
        log(
            f"[REPORT] fingerprint='{after_fp}', "
            f"tags='{after_tags}', "
            f"type='{after_type}'"
        )

        return {
            "fingerprint": after_fp,
            "tags": after_tags,
            "type": after_type,
        }

    except Exception as e:
        log(f"[L3] ERROR: L3 pipeline failed: {e}")
        return None
