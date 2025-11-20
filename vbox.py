import subprocess
import time
from typing import Dict

def run(cmd: list[str]) -> None:
    subprocess.check_call(cmd, shell=False)

def clone_vm(base_name: str, new_name: str) -> None:
    run(["VBoxManage", "clonevm", base_name, "--name", new_name, "--register"])

def set_resources(vm: str, res: Dict) -> None:
    ram = int(res.get("ram", 2048))
    vram = int(res.get("vram", 64))
    cpus = int(res.get("cpus", 2))
    run(["VBoxManage", "modifyvm", vm, "--memory", str(ram), "--vram", str(vram), "--cpus", str(cpus)])

def ensure_graphics_controller(vm: str, controller: str = "vboxvga", vram: int = 64, accel3d: bool = True) -> None:
    args = ["VBoxManage", "modifyvm", vm, "--graphicscontroller", controller, "--vram", str(vram)]
    if accel3d:
        args += ["--accelerate3d", "on"]
    run(args)

def ensure_boot_disk_first(vm: str) -> None:
    run(["VBoxManage", "modifyvm", vm, "--boot1", "disk", "--boot2", "none", "--boot3", "none", "--boot4", "none"])

def start_vm(vm: str, gui: bool = True) -> None:
    run(["VBoxManage", "startvm", vm, "--type", "gui" if gui else "headless"])
    time.sleep(2)
