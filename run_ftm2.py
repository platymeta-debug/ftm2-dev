# -*- coding: utf-8 -*-
"""
run_ftm2.py - Windows에서도 클릭 한 번으로 실행되도록 .env/token.env를 자동 로드하고 ftm2.app을 구동합니다.
"""
import os, sys, runpy

def load_env_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"): 
                    continue
                if "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                # 이미 OS에 있는 값은 덮어쓰지 않음
                if k and (os.environ.get(k) is None or os.environ.get(k) == ""):
                    os.environ[k] = v
    except FileNotFoundError:
        pass

def main():
    base = os.path.dirname(os.path.abspath(__file__))
    # .env, token.env 자동 로드
    load_env_file(os.path.join(base, ".env"))
    load_env_file(os.path.join(base, "token.env"))

    # 기본값(없을 때만)
    os.environ.setdefault("OPS_HTTP_ENABLED", "true")
    os.environ.setdefault("OPS_HTTP_PORT", "8080")

    print("[FTM2] starting... DATA_MODE=%s TRADE_MODE=%s" % (
        os.getenv("DATA_MODE", "live"), os.getenv("TRADE_MODE", "dry")
    ))
    # ftm2.app 실행
    runpy.run_module("ftm2.app", run_name="__main__")

if __name__ == "__main__":
    main()
