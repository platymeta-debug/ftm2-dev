# -*- coding: utf-8 -*-
"""
run_ftm2.py - Windows에서도 클릭 한 번으로 실행되도록 .env/token.env를 자동 로드하고 ftm2.app을 구동합니다.
"""
import os, sys, runpy, logging

# [ANCHOR:ENV_LOADER] begin
log = logging.getLogger("ftm2.env")

def _mask(s, keep: int = 4) -> str:
    if not s:
        return ""
    s = str(s)
    return s[:keep] + "*" * max(0, len(s) - keep - 2) + s[-2:]


def load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=os.getenv("ENV_PATH", ".env"), override=True)
    except Exception:
        pass

    tm = os.getenv("TRADE_MODE", "testnet")
    use_user = os.getenv("USE_USER", "")
    k = os.getenv("BINANCE_TESTNET_API_KEY") or os.getenv("BINANCE_API_KEY")
    s = os.getenv("BINANCE_TESTNET_API_SECRET") or os.getenv("BINANCE_API_SECRET")
    log.info(
        "[BOOT_ENV_SUMMARY] TRADE_MODE=%s USE_USER=%r KEY=%s SECRET=%s",
        tm,
        use_user,
        _mask(k),
        _mask(s),
    )


load_env()
# [ANCHOR:ENV_LOADER] end


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
                if k and (os.environ.get(k) is None or os.environ.get(k) == ""):
                    os.environ[k] = v
    except FileNotFoundError:
        pass

def main():
    base = os.path.dirname(os.path.abspath(__file__))
    # token.env 자동 로드 (.env는 load_env에서 처리)
    load_env_file(os.path.join(base, "token.env"))

    # 기본값(없을 때만)
    os.environ.setdefault("OPS_HTTP_ENABLED", "true")
    os.environ.setdefault("OPS_HTTP_PORT", "8080")
    os.environ.setdefault("DISCORD_ENABLED", "true")

    print("[FTM2] starting... DATA_MODE=%s TRADE_MODE=%s" % (
        os.getenv("DATA_MODE", "live"), os.getenv("TRADE_MODE", "dry")
    ))
    # ftm2.app 실행
    runpy.run_module("ftm2.app", run_name="__main__")

if __name__ == "__main__":
    main()
