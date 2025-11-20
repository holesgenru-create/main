import os, json, random, string, pathlib

CATALOG_PATH = pathlib.Path(__file__).with_name("catalog.json")

def _rand_hex(n: int) -> str:
    import secrets
    return secrets.token_hex(n//2) if n % 2 == 0 else (secrets.token_hex(n//2) + secrets.token_hex(1)[0])

def _rand_serial(n: int = 14) -> str:
    import random, string
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(random.choice(alphabet) for _ in range(n))

def load_catalog():
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def generate_profile(profile_key: str | None = None) -> dict:
    cat = load_catalog()
    chosen = None
    if profile_key and profile_key != "random":
        for p in cat:
            if p.get("key") == profile_key:
                chosen = p
                break
    if not chosen:
        import random
        chosen = random.choice(cat)
    # core copy
    profile = dict(chosen)
    # dynamic ids
    profile["ANDROID_ID"] = _rand_hex(16)
    profile["ro.serialno"] = _rand_serial(14)
    return profile
