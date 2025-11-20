import subprocess
import time
import traceback
from typing import Callable, Optional

import pyotp
import uiautomator2 as u2

from app.device_ui import get_device, sleep_human, type_text_human, click_text, click_id


PKG_INSTAGRAM = "com.instagram.android"


def _run_adb(serial: str, args: list[str]) -> None:
    cmd = ["adb", "-s", serial] + args
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _find_edittext_pair(d: u2.Device, log: Callable[[str], None]):
    """
    Возвращает (username_elem, password_elem).

    Логика:
    1) Пытаемся по resource-id.
    2) Если не нашли — берём EditText instance 0 и 1.
    3) Если и этого нет — пробуем textContains (как крайний случай).
    """
    username_ids = [
        "com.instagram.android:id/login_username",
        "com.instagram.android:id/email_field",
        "com.instagram.android:id/loginUsernameField",
    ]
    password_ids = [
        "com.instagram.android:id/password",
        "com.instagram.android:id/password_field",
    ]

    username_elem = None
    password_elem = None

    # 1. По resource-id
    for rid in username_ids:
        obj = d(resourceId=rid)
        if obj.exists:
            log(f"[IG] Нашёл поле логина по resource-id='{rid}'.")
            username_elem = obj
            break

    for rid in password_ids:
        obj = d(resourceId=rid)
        if obj.exists:
            log(f"[IG] Нашёл поле пароля по resource-id='{rid}'.")
            password_elem = obj
            break

    # 2. По EditText instance 0/1
    if username_elem is None:
        et0 = d(className="android.widget.EditText", instance=0)
        if et0.exists:
            log("[IG] Нашёл поле логина как EditText instance 0.")
            username_elem = et0

    if password_elem is None:
        et1 = d(className="android.widget.EditText", instance=1)
        if et1.exists:
            log("[IG] Нашёл поле пароля как EditText instance 1.")
            password_elem = et1

    # 3. Крайний случай — textContains
    if username_elem is None:
        for txt in [
            "Username",
            "email or mobile",
            "Phone number, email or username",
            "Phone number, username or email",
        ]:
            obj = d(textContains=txt)
            if obj.exists:
                log(f"[IG] Нашёл поле логина по textContains='{txt}'.")
                username_elem = obj
                break

    if password_elem is None:
        for txt in ["Password"]:
            obj = d(textContains=txt)
            if obj.exists:
                log(f"[IG] Нашёл поле пароля по textContains='{txt}'.")
                password_elem = obj
                break

    if username_elem is None or password_elem is None:
        raise RuntimeError("Не удалось найти поля логина и/или пароля.")

    return username_elem, password_elem


def _scroll_to_continue(d: u2.Device, log: Callable[[str], None]) -> None:
    """
    Прокрутка экрана вниз, если кнопка 'Continue' находится ниже видимой области.
    """
    try:
        click_text(d, "Continue", log, timeout=3.0)
        log("[IG] Кнопка 'Continue' найдена без скролла.")
        return
    except RuntimeError:
        log("[IG] Кнопка 'Continue' сразу не найдена, пробую пролистать вниз.")

    scrollable = d(scrollable=True)
    if not scrollable.exists:
        log("[IG] Scrollable контейнер не найден, пропускаю скролл.")
        return

    for i in range(3):
        try:
            scrollable.scroll(steps=15)
        except Exception:
            try:
                scrollable.fling.vert.forward()
            except Exception:
                pass
        sleep_human(0.4, 0.7)
        try:
            click_text(d, "Continue", log, timeout=2.0)
            log("[IG] Кнопка 'Continue' найдена после скролла.")
            return
        except RuntimeError:
            log(f"[IG] Попытка скролла #{i + 1} не помогла, пробую дальше.")

    log("[IG] WARNING: не удалось нажать 'Continue' даже после прокрутки.")


def login_instagram(
    serial: str,
    login: str,
    password: str,
    totp_secret: Optional[str],
    log: Callable[[str], None],
) -> None:
    """
    Основной сценарий логина в Instagram через ADB + uiautomator2.
    Бросает RuntimeError при ошибке, чтобы auth_accounts_task мог пометить клон как FAILED.
    """
    log(f"[IG] step 1/8: open Instagram app on {serial}")

    # Страхуемся: сначала убьём приложение, потом запустим
    try:
        _run_adb(serial, ["shell", "am", "force-stop", PKG_INSTAGRAM])
    except Exception:
        pass

    _run_adb(
        serial,
        [
            "shell",
            "monkey",
            "-p",
            PKG_INSTAGRAM,
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
        ],
    )

    sleep_human(3.0, 5.0)

    d = get_device(serial)

    # step 2/8: welcome / стартовый экран
    log("[IG] step 2/8: handle welcome screen")

    try:
        join_screen = d(textContains="Join Instagram")
        already_have = d(textContains="I already have an account")
        log_in_button = d(text="Log in")

        if already_have.exists:
            log("[IG] На стартовом экране есть 'I already have an account' — нажимаю.")
            click_text(d, "I already have an account", log, timeout=8.0)
        elif log_in_button.exists:
            # Иногда сразу логин-форма, кнопка 'Log in' как CTA
            log("[IG] На стартовом экране есть кнопка 'Log in' — нажимаю.")
            click_text(d, "Log in", log, timeout=8.0)
        else:
            log("[IG] Первый экран не требует действий, продолжаю.")
    except Exception as e:
        log(f"[IG] WARNING: ошибка при обработке стартового экрана: {e}")

    sleep_human(2.0, 3.5)

    # step 3/8: ввод логина/пароля
    log("[IG] step 3/8: fill login & password")

    username_elem, password_elem = _find_edittext_pair(d, log)

    log("[IG] Ввожу логин.")
    type_text_human(username_elem, login, log)
    sleep_human(0.5, 1.0)

    log("[IG] Ввожу пароль.")
    type_text_human(password_elem, password, log)
    sleep_human(0.5, 1.0)

    # step 4/8: кнопка Log in
    log("[IG] step 4/8: press 'Log in' button")

    # Сначала пробуем по id, потом по text
    pressed = False
    for rid in [
        "com.instagram.android:id/next_button",
        "com.instagram.android:id/button_text",
    ]:
        try:
            click_id(d, rid, log, timeout=3.0)
            pressed = True
            break
        except RuntimeError:
            continue

    if not pressed:
        try:
            click_text(d, "Log in", log, timeout=5.0)
            pressed = True
        except RuntimeError:
            log("[IG] ERROR: не удалось найти кнопку 'Log in'.")
            raise RuntimeError("Не удалось нажать кнопку 'Log in'.")

    log("[IG] Нажал кнопку 'Log in'.")
    sleep_human(4.0, 6.0)

    # step 5/8: экран "Check your notifications"
    log("[IG] step 5/8: handle 'Check your notifications' screen (if any)")

    try:
        if d(textContains="Check your notifications").exists:
            log("[IG] Обнаружен экран 'Check your notifications', жму 'Try another way'.")
            click_text(d, "Try another way", log, timeout=8.0)
        else:
            log("[IG] Экран 'Check your notifications' не появился.")
    except Exception as e:
        log(f"[IG] WARNING: ошибка обработки экрана уведомлений: {e}")

    sleep_human(2.0, 3.5)

    # step 6/8: выбор метода 2FA "Authentication app"
    log("[IG] step 6/8: choose 'Authentication app' 2FA method (if screen is shown)")

    try:
        if d(textContains="Choose a way to confirm").exists or d(
            textContains="Authentication app"
        ).exists:
            log("[IG] Обнаружен экран выбора метода 2FA, выбираю 'Authentication app'.")
            radio = d(textContains="Authentication app")
            if radio.exists:
                radio.click()
                sleep_human(0.3, 0.7)

            _scroll_to_continue(d, log)
        else:
            log("[IG] Экран выбора метода 2FA не появился, двигаемся дальше.")
    except Exception as e:
        log(f"[IG] WARNING: ошибка обработки выбора 2FA: {e}")

    sleep_human(2.0, 3.5)

    # step 7/8: ввод TOTP, если есть экран с кодом
    log("[IG] step 7/8: handle 2FA TOTP screen if present.")

    try:
        if d(textContains="Enter the 6-digit code").exists or d(text="Code").exists:
            log("[IG] Обнаружен экран TOTP.")
            if not totp_secret:
                raise RuntimeError("2FA требуется, но totp_secret не задан.")

            # Генерируем код, маскируем в логах
            totp = pyotp.TOTP(totp_secret.replace(" ", ""))
            code = totp.now()
            log("[IG] Ввожу TOTP-код (маскируется в логах).")

            # Пытаемся найти поле ввода кода
            code_field = None

            # Сначала EditText
            et0 = d(className="android.widget.EditText", instance=0)
            if et0.exists:
                code_field = et0
                log("[IG] Нашёл поле ввода кода как EditText instance 0.")
            else:
                for txt in ["Code"]:
                    obj = d(textContains=txt)
                    if obj.exists:
                        code_field = obj
                        log(f"[IG] Нашёл поле ввода кода по textContains='{txt}'.")
                        break

            if code_field is None:
                raise RuntimeError("Не удалось найти поле ввода TOTP-кода.")

            type_text_human(code_field, code, log)

            # Кнопка Continue может быть внизу — используем скролл-хелпер
            _scroll_to_continue(d, log)
        else:
            log("[IG] Экран TOTP не появился, шаг 7 пропущен.")
    except Exception as e:
        log(f"[IG] ERROR: ошибка при обработке TOTP: {e}")
        raise

    sleep_human(2.0, 3.5)

    # step 8/8: "Save your login info?"
    log("[IG] step 8/8: handle 'Save your login info?' screen if present.")

    try:
        # --- ОБНОВЛЁННАЯ ЛОГИКА ---
        # Ждём появления заголовка или кнопки "Not now" с таймаутом.
        header_candidates = [
            d(textContains="Save your login info"),
            d(textContains="Save login info"),
        ]
        screen_found = False
        for obj in header_candidates:
            if obj.wait(timeout=6.0):
                screen_found = True
                break

        btn_not_now = d(text="Not now")
        if not screen_found:
            # Если не нашли заголовок, но появилась кнопка — всё равно считаем, что это этот экран.
            if btn_not_now.wait(timeout=6.0):
                screen_found = True

        if screen_found:
            log("[IG] Обнаружен экран 'Save your login info?', жму 'Not now'.")
            try:
                click_text(d, "Not now", log, timeout=6.0)
            except RuntimeError:
                log("[IG] WARNING: не удалось нажать 'Not now' через click_text, пробую прямой клик.")
                if btn_not_now.exists:
                    btn_not_now.click()
                    sleep_human(0.3, 0.7)
        else:
            log("[IG] Экран 'Save your login info?' не появился.")
        # --- КОНЕЦ ОБНОВЛЁННОЙ ЛОГИКИ ---
    except Exception as e:
        log(f"[IG] WARNING: ошибка обработки экрана 'Save your login info?': {e}")

    sleep_human(3.0, 4.5)

    # Финальная проверка: есть ли нижнее меню
    log("[IG] Финальная проверка успешной авторизации (нижнее меню).")

    try:
        tab_ids = [
            "com.instagram.android:id/tab_bar",
            "com.instagram.android:id/bottom_navigation",
        ]
        found_tab = False
        for rid in tab_ids:
            if d(resourceId=rid).exists:
                found_tab = True
                break

        # Дополнительная эвристика: иконки Home/Search/Reels
        if not found_tab:
            for txt in ["Home", "Reels", "Search"]:
                if d(descriptionContains=txt).exists or d(textContains=txt).exists:
                    found_tab = True
                    break

        if not found_tab:
            raise RuntimeError(
                "Не удалось подтвердить успешный логин (нижнее меню не найдено)"
            )

        log(f"[IG] SUCCESS: login completed for {login[:2]}***.")
    except Exception as e:
        log(f"[IG] ERROR: login failed for {login[:2]}***: {e}")
        raise
