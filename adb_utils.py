import subprocess, time

def _run(cmd: list[str]):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, shell=False)

def adb_serial(host: str, port: int) -> str:
    return f"{host}:{port}"

def adb_connect(host: str, port: int) -> bool:
    out = _run(["adb", "connect", f"{host}:{port}"]).stdout
    return "connected" in out.lower() or "already connected" in out.lower()

def wait_for_device(host: str, port: int, timeout: int = 180) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        if adb_connect(host, port):
            out = _run(["adb", "devices"]).stdout
            if adb_serial(host, port) in out:
                return True
        time.sleep(3)
    return False

def adb_shell(host: str, port: int, *args: str) -> str:
    ser = adb_serial(host, port)
    proc = _run(["adb", "-s", ser, "shell", *args])
    return proc.stdout

def adb_install(host: str, port: int, apk_path: str) -> str:
    ser = adb_serial(host, port)
    proc = _run(["adb", "-s", ser, "install", "-r", apk_path])
    return proc.stdout
