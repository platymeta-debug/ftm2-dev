import os

def _clean(v: str|None) -> str|None:
    if v is None:
        return None
    # '#' 주석 제거 + 좌우 공백 제거
    return v.split('#', 1)[0].strip()

def env_str(key: str, default: str|None=None) -> str|None:
    v = _clean(os.getenv(key))
    return v if (v is not None and v != "") else default

def env_int(key: str, default: int=0) -> int:
    v = env_str(key, None)
    if v is None: return default
    try:
        return int(v)
    except Exception:
        return default

def env_float(key: str, default: float=0.0) -> float:
    v = env_str(key, None)
    if v is None: return default
    try:
        return float(v)
    except Exception:
        return default

def env_bool(key: str, default: bool=False) -> bool:
    v = env_str(key, None)
    if v is None: return default
    return v.lower() in ("1","true","on","yes","y")

def env_list(key: str, sep: str=",") -> list[str]:
    v = env_str(key, "")
    return [x.strip() for x in v.split(sep) if x.strip()]
