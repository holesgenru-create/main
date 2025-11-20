import subprocess
from typing import List

def run(args: List[str], check: bool=False):
    return subprocess.run(args, text=True, capture_output=True, check=check)

def safe_run(args: List[str]):
    p = run(args, check=False)
    return p.returncode, p.stdout, p.stderr
