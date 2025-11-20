// ui.js – фронт для Unikit L2 (Stage 1 + Stage 2)

(function () {
    "use strict";

    function appendLog(textarea, line) {
        if (!textarea) {
            return;
        }
        if (textarea.value.length > 0 && !textarea.value.endsWith("\n")) {
            textarea.value += "\n";
        }
        textarea.value += line;
        textarea.scrollTop = textarea.scrollHeight;
    }

    function formatLogMessage(raw) {
        try {
            const obj = JSON.parse(raw);
            if (obj && typeof obj === "object" && "message" in obj) {
                const ts = obj.ts || "";
                const level = obj.level || "INFO";
                return "[" + ts + "] [" + level + "] " + obj.message;
            }
        } catch (e) {
        }
        return raw;
    }

    function createWebSocket(jobId, onLine) {
        const wsScheme = window.location.protocol === "https:" ? "wss" : "ws";
        const url = wsScheme + "://" + window.location.host + "/ws/logs?job_id=" + encodeURIComponent(jobId);
        const ws = new WebSocket(url);

        ws.onmessage = function (event) {
            const line = formatLogMessage(event.data);
            onLine(line);
            if (line.indexOf("__DONE__") !== -1) {
                try {
                    ws.close();
                } catch (e) {
                }
            }
        };

        ws.onopen = function () {
            onLine("[UI] WebSocket открыт для job_id=" + jobId);
        };

        ws.onclose = function () {
            onLine("[UI] WebSocket закрыт для job_id=" + jobId);
        };

        ws.onerror = function (event) {
            onLine("[UI] WebSocket error: " + String(event));
        };

        return ws;
    }

    document.addEventListener("DOMContentLoaded", function () {
        const tabDevicesBtn = document.getElementById("tab-devices");
        const tabAuthBtn = document.getElementById("tab-auth");
        const tabDevicesContent = document.getElementById("tab-devices-content");
        const tabAuthContent = document.getElementById("tab-auth-content");

        function activateTab(name) {
            if (name === "devices") {
                tabDevicesBtn.classList.add("active");
                tabAuthBtn.classList.remove("active");
                tabDevicesContent.classList.add("active");
                tabAuthContent.classList.remove("active");
            } else {
                tabDevicesBtn.classList.remove("active");
                tabAuthBtn.classList.add("active");
                tabDevicesContent.classList.remove("active");
                tabAuthContent.classList.add("active");
            }
        }

        tabDevicesBtn.addEventListener("click", function () {
            activateTab("devices");
        });

        tabAuthBtn.addEventListener("click", function () {
            activateTab("auth");
        });

        const devicesCountSelect = document.getElementById("devices-count");
        const devicesModelSelect = document.getElementById("devices-model");
        const devicesAdbStartInput = document.getElementById("devices-adb-start");
        const devicesApkInput = document.getElementById("devices-apk");
        const devicesStartBtn = document.getElementById("devices-start-btn");
        const devicesJobIdSpan = document.getElementById("devices-job-id");
        const devicesLog = document.getElementById("devices-log");

        const authJobIdInput = document.getElementById("auth-job-id-input");

        let devicesWs = null;
        let lastDevicesJobId = null;

        function loadModels() {
            appendLog(devicesLog, "[UI] Загружаю список моделей...");
            fetch("/api/models")
                .then(function (resp) {
                    if (!resp.ok) {
                        throw new Error("HTTP " + resp.status);
                    }
                    return resp.json();
                })
                .then(function (data) {
                    devicesModelSelect.innerHTML = "";
                    if (!data || !Array.isArray(data.models)) {
                        throw new Error("Некорректный ответ /api/models");
                    }
                    data.models.forEach(function (m) {
                        const opt = document.createElement("option");
                        opt.value = m.key;
                        opt.textContent = m.name + " (" + m.key + ")";
                        devicesModelSelect.appendChild(opt);
                    });
                    appendLog(devicesLog, "[UI] Список моделей загружен.");
                })
                .catch(function (err) {
                    appendLog(devicesLog, "[UI] Ошибка загрузки моделей: " + err.message);
                });
        }

        loadModels();

        devicesStartBtn.addEventListener("click", function () {
            const count = parseInt(devicesCountSelect.value || "1", 10);
            const modelKey = devicesModelSelect.value;
            const adbStart = parseInt(devicesAdbStartInput.value || "5555", 10);

            if (!modelKey) {
                appendLog(devicesLog, "[UI] Выберите модель.");
                return;
            }

            const formData = new FormData();
            formData.append("count", String(count));
            formData.append("model_key", modelKey);
            formData.append("adb_start", String(adbStart));

            if (devicesApkInput.files && devicesApkInput.files.length > 0) {
                formData.append("apk", devicesApkInput.files[0]);
            }

            appendLog(devicesLog, "[UI] Отправляю запрос на создание устройств...");

            fetch("/api/devices/create", {
                method: "POST",
                body: formData
            })
                .then(function (resp) {
                    if (!resp.ok) {
                        throw new Error("HTTP " + resp.status);
                    }
                    return resp.json();
                })
                .then(function (data) {
                    const jobId = data.job_id;
                    lastDevicesJobId = jobId;
                    devicesJobIdSpan.textContent = jobId;
                    if (authJobIdInput) {
                        authJobIdInput.value = jobId;
                    }
                    appendLog(devicesLog, "[UI] Задача запущена, job_id=" + jobId);

                    if (devicesWs) {
                        try {
                            devicesWs.close();
                        } catch (e) {
                        }
                        devicesWs = null;
                    }

                    devicesWs = createWebSocket(jobId, function (line) {
                        appendLog(devicesLog, line);
                    });
                })
                .catch(function (err) {
                    appendLog(devicesLog, "[UI] Ошибка запуска: " + err.message);
                });
        });

        appendLog(devicesLog, "[UI] Готово к запуску.");

        const authFileInput = document.getElementById("auth-file");
        const authFileInfo = document.getElementById("auth-file-info");
        const authStartBtn = document.getElementById("auth-start-btn");
        const authJobIdLabel = document.getElementById("auth-job-id-label");
        const authLog = document.getElementById("auth-log");

        let authWs = null;
        let currentAccountsId = null;

        authFileInput.addEventListener("change", function () {
            if (!authFileInput.files || authFileInput.files.length === 0) {
                currentAccountsId = null;
                authFileInfo.textContent = "Файл не выбран.";
                return;
            }

            const file = authFileInput.files[0];
            const formData = new FormData();
            formData.append("file", file);

            appendLog(authLog, "[AUTH UI] Загружаю файл аккаунтов...");

            fetch("/api/auth/upload", {
                method: "POST",
                body: formData
            })
                .then(function (resp) {
                    if (!resp.ok) {
                        throw new Error("HTTP " + resp.status);
                    }
                    return resp.json();
                })
                .then(function (data) {
                    currentAccountsId = data.accounts_id;
                    const lines = data.lines;
                    authFileInfo.textContent =
                        "Файл: " + file.name +
                        " | accounts_id: " + currentAccountsId +
                        " | строк: " + lines;
                    appendLog(authLog, "[AUTH UI] Файл загружен, accounts_id=" + currentAccountsId + ", строк=" + lines);
                })
                .catch(function (err) {
                    appendLog(authLog, "[AUTH UI] Ошибка загрузки файла: " + err.message);
                });
        });

        authStartBtn.addEventListener("click", function () {
            if (!currentAccountsId) {
                appendLog(authLog, "[AUTH UI] Сначала выберите и загрузите файл аккаунтов.");
                return;
            }

            const jobId = (authJobIdInput.value || "").trim();
            if (!jobId) {
                appendLog(authLog, "[AUTH UI] Укажите job_id устройств (Stage 1).");
                return;
            }

            authJobIdLabel.textContent = jobId;
            appendLog(authLog, "[AUTH UI] Запускаю авторизацию, job_id=" + jobId + ", accounts_id=" + currentAccountsId);

            const payload = {
                job_id: jobId,
                accounts_id: currentAccountsId,
                mode: "one_to_one"
            };

            fetch("/api/auth/start", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify(payload)
            })
                .then(function (resp) {
                    if (!resp.ok) {
                        return resp.json().then(function (errBody) {
                            const detail = errBody && errBody.detail ? String(errBody.detail) : "HTTP " + resp.status;
                            throw new Error(detail);
                        });
                    }
                    return resp.json();
                })
                .then(function (data) {
                    appendLog(authLog, "[AUTH UI] Таска авторизации запущена: status=" + data.status + ", job_id=" + data.job_id);

                    if (authWs) {
                        try {
                            authWs.close();
                        } catch (e) {
                        }
                        authWs = null;
                    }

                    authWs = createWebSocket(jobId, function (line) {
                        appendLog(authLog, line);
                    });
                })
                .catch(function (err) {
                    appendLog(authLog, "[AUTH UI] Ошибка старта авторизации: " + err.message);
                });
        });

        appendLog(authLog, "[AUTH UI] Вкладка готова. Загрузите файл аккаунтов и укажите job_id устройств.");
    });
})();