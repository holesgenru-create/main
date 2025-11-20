
import subprocess, time, shlex, logging
log = logging.getLogger(__name__)
class ADBError(Exception): pass
def _run(cmd, timeout=300, check=True, capture=True):
    proc = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.STDOUT, timeout=timeout, encoding="utf-8", errors="replace")
    out = proc.stdout or ""
    if check and proc.returncode != 0:
        raise ADBError(f"Command failed ({proc.returncode}): {cmd}\n{out}")
    return out.strip()
def adb_connect(serial):
    if ":" in serial: _run(f"adb connect {serial}")
    for _ in range(120):
        out = _run("adb devices")
        if serial in out: return
        time.sleep(1)
    raise ADBError(f"Device {serial} did not connect")
def adb_wait(serial): _run(f"adb -s {serial} wait-for-device")
def adb_shell(serial, cmd, as_root=False):
    if as_root: full = f'adb -s {serial} shell "su 0 sh -c {shlex.quote(cmd)}"'
    else: full = f'adb -s {serial} shell {shlex.quote(cmd)}'
    return _run(full)
def adb_push(serial, src, dst): return _run(f'adb -s {serial} push {shlex.quote(src)} {shlex.quote(dst)}')
def adb_install(serial, apk_path, replace=True):
    flags = "-r " if replace else ""
    return _run(f'adb -s {serial} install {flags}{shlex.quote(apk_path)}')
def adb_getprop(serial, name): return adb_shell(serial, f"getprop {shlex.quote(name)}").strip()
def adb_setprop_runtime(serial, name, value):
    return adb_shell(serial, f'/sbin/resetprop {shlex.quote(name)} {shlex.quote(str(value))}', as_root=True)
def adb_boot_completed(serial, timeout_s=300):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if adb_getprop(serial, "sys.boot_completed").strip() == "1": return True
        except Exception: pass
        time.sleep(2)
    return False
