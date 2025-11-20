import random
import time
from typing import Callable

import uiautomator2 as u2


def get_device(serial: str):
    """
    Connect to device by serial and enable fast input IME if possible.
    """
    d = u2.connect(serial)
    try:
        # Faster and более стабильный ввод текста
        d.set_fastinput_ime(True)
    except Exception:
        # Не критично, если не получилось
        pass
    return d


def sleep_human(min_seconds: float, max_seconds: float) -> None:
    """
    Human-like sleep between actions.
    """
    delay = random.uniform(min_seconds, max_seconds)
    time.sleep(delay)


def type_text_human(elem, text: str, log: Callable[[str], None]) -> None:
    """
    Human-like text input into given UiObject.

    Важно:
    - Не меняем сигнатуру (elem, text, log).
    - Сначала кликаем по элементу, потом несколько раз вызываем set_text(text),
      чтобы гарантировать наличие итогового текста.
    """
    if not text:
        return

    try:
        elem.click()
    except Exception as e:
        log(f"[IG] WARNING: не удалось кликнуть по элементу перед вводом: {e}")

    sleep_human(0.3, 0.7)

    length = len(text)
    log(f"[IG] Начинаю посимвольный ввод текста длиной {length}.")

    current = ""
    for idx, ch in enumerate(text, start=1):
        current += ch
        try:
            # Пробуем постепенно наращивать текст
            elem.set_text(current)
        except Exception as e:
            log(f"[IG] ERROR: set_text на шаге {idx}: {e}")
            # Одно падение считаем критичным: дальше смысла нет
            raise
        # Мелкая задержка между «нажатиями»
        sleep_human(0.05, 0.15)
        # Иногда делаем подлиннее паузу
        if idx % random.randint(4, 7) == 0:
            sleep_human(0.25, 0.5)

    # Финальный дубль: гарантируем, что в поле ровно весь текст
    try:
        elem.set_text(text)
    except Exception:
        # Если финальный set_text упал — уже не критично, текст и так должен быть
        pass

    log("[IG] Посимвольный ввод завершён.")


def click_text(d, text: str, log: Callable[[str], None], timeout: float = 10.0) -> None:
    """
    Wait for element with given text (or textContains) and click it.
    Raises RuntimeError if not found.
    """
    log(f"[IG] Ищу элемент с text='{text}' (timeout={timeout}s).")
    obj = d(text=text)
    if not obj.exists:
        obj = d(textContains=text)

    if not obj.wait(timeout=timeout):
        log(f"[IG] ERROR: элемент с text='{text}' не найден за {timeout} секунд.")
        raise RuntimeError(f"Element with text '{text}' not found")

    sleep_human(0.2, 0.7)
    obj.click()
    log(f"[IG] Клик по элементу с text='{text}' выполнен.")


def click_id(d, res_id: str, log: Callable[[str], None], timeout: float = 10.0) -> None:
    """
    Wait for element with given resource-id and click it.
    Raises RuntimeError if not found.
    """
    log(f"[IG] Ищу элемент с id='{res_id}' (timeout={timeout}s).")
    obj = d(resourceId=res_id)

    if not obj.wait(timeout=timeout):
        log(f"[IG] ERROR: элемент с id='{res_id}' не найден за {timeout} секунд.")
        raise RuntimeError(f"Element with id '{res_id}' not found")

    sleep_human(0.2, 0.7)
    obj.click()
    log(f"[IG] Клик по элементу с id='{res_id}' выполнен.")
