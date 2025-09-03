# -*- coding: utf-8 -*-
"""
간단 ENV 체인 로더
- 우선순위: os.environ > token.env > .env
- 파일이 없으면 무시. 값은 'KEY=VALUE' 형태만 인식.
"""
from __future__ import annotations
import os
from typing import Dict, Tuple

# [ANCHOR:ENV_LOADER]
def _parse_env_file(path: str) -> Dict[str, str]:
    kv: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                kv[k] = v
    except FileNotFoundError:
        pass
    return kv

def load_env_chain(paths: Tuple[str, ...] = ("token.env", ".env")) -> Dict[str, str]:
    # 1) 시작은 현재 OS 환경을 복사
    env: Dict[str, str] = dict(os.environ)

    # 2) token.env → .env 순서로, **존재하지 않는 키만** 주입
    for p in paths:
        kv = _parse_env_file(p)
        for k, v in kv.items():
            if k not in env or env.get(k) in (None, ""):
                os.environ.setdefault(k, v)
                env.setdefault(k, v)

    return env
