
import time
from .adb import adb_shell, adb_push, adb_setprop_runtime, adb_getprop
RUNTIME_APPLY_ORDER = ["ro.build.fingerprint","ro.build.tags","ro.build.type"]
def build_prop_text(prop_dict): return "\n".join(f"{k}={v}" for k,v in prop_dict.items()) + "\n"
def apply_device_profile(serial, prop_dict, logger=None):
    import tempfile, os
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
        f.write(build_prop_text(prop_dict)); tmp = f.name
    try:
        adb_push(serial, tmp, "/data/local/tmp/device_profile.prop")
        hook_exists=False
        try:
            out = adb_shell(serial, "ls -l /sbin/l3-apply.sh", as_root=True)
            if "l3-apply.sh" in out: hook_exists=True
        except Exception: hook_exists=False
        if hook_exists: adb_shell(serial, "/sbin/l3-apply.sh", as_root=True)
        else: adb_shell(serial, "/sbin/resetprop -f /data/local/tmp/device_profile.prop", as_root=True)
        for k in RUNTIME_APPLY_ORDER:
            if k in prop_dict: adb_setprop_runtime(serial, k, prop_dict[k])
        time.sleep(0.5)
        res = {}
        for k in prop_dict.keys():
            try: res[k]=adb_getprop(serial,k)
            except Exception: res[k]=None
        return res
    finally:
        try: os.unlink(tmp)
        except Exception: pass
