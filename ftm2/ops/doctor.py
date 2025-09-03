# -*- coding: utf-8 -*-
"""
FTM2 Doctor — 운영 전 점검 CLI
사용:
  python -m ftm2.ops.doctor
옵션:
  --no-live        라이브 엔드포인트 핑 생략
  --no-testnet     테스트넷 엔드포인트 핑 생략
  --port 8080      /healthz 점검 포트(기본 8080)
  --db ./data/ftm2.sqlite  DB 경로 지정(기본 ENV DB_PATH 또는 ./data/ftm2.sqlite)
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple
import os, sys, time, json, sqlite3, socket, urllib.request

LIVE_PING = "https://fapi.binance.com/fapi/v1/ping"
TEST_PING = "https://testnet.binancefuture.com/fapi/v1/ping"

@dataclass
class DoctorConfig:
    check_live: bool = True
    check_testnet: bool = True
    ops_port: int = 8080
    db_path: Optional[str] = None
    timeout_s: float = 3.0

def _env(k: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(k)
    return v if v not in (None, "") else default

def _http_get(url: str, timeout: float) -> Tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            code = getattr(r, "status", 200)
            return (200 <= code < 300, f"HTTP {code}")
    except Exception as e:
        return (False, f"ERR {type(e).__name__}: {e}")

def _check_env_vars(keys: List[str]) -> List[str]:
    missing = []
    for k in keys:
        if _env(k) is None:
            missing.append(k)
    return missing

def _check_db(path: str) -> Tuple[bool, str]:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        cx = sqlite3.connect(path, timeout=2.0)
        cur = cx.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS _doctor_probe (ts INTEGER)")
        cur.execute("DELETE FROM _doctor_probe")
        cur.execute("INSERT INTO _doctor_probe(ts) VALUES (?)", (int(time.time()*1000),))
        cx.commit()
        cx.close()
        return (True, "쓰기 OK")
    except Exception as e:
        return (False, f"ERR {type(e).__name__}: {e}")

def _check_port_or_healthz(port: int, timeout: float) -> Tuple[str, str]:
    # 1) /healthz가 살아있으면 OK(이미 구동 중)
    ok, msg = _http_get(f"http://127.0.0.1:{port}/healthz", timeout)
    if ok:
        return ("OK", "서비스 동작 중(/healthz 200)")
    # 2) 아니면 포트 가용성 확인(열 수 있으면 OK=대기중)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        res = s.connect_ex(("127.0.0.1", port))
        if res != 0:
            return ("OK", "포트 사용 가능(서비스 미가동)")
        else:
            return ("WARN", "포트 사용 중(하지만 /healthz 없음) — 다른 프로세스 점유?")
    except Exception as e:
        return ("WARN", f"포트 검사 실패: {e}")
    finally:
        try: s.close()
        except Exception: pass

def run_checks(cfg: DoctorConfig) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    # 0) 파이썬/기본 폴더
    checks.append({"name":"python", "status":"OK", "msg": f"{sys.version.split()[0]} (UTF-8)"})
    for d in ("./data", "./logs"):
        try:
            os.makedirs(d, exist_ok=True)
            checks.append({"name":f"dir:{d}", "status":"OK", "msg":"존재/쓰기 OK"})
        except Exception as e:
            checks.append({"name":f"dir:{d}", "status":"FAIL", "msg":str(e)})

    # 1) 모드 유효성
    dm = (_env("DATA_MODE","live") or "").lower()
    tm = (_env("TRADE_MODE","dry") or "").lower()
    dm_ok = dm in ("live","testnet","replay")
    tm_ok = tm in ("dry","testnet","live")
    msg = f"DATA_MODE={dm}, TRADE_MODE={tm}"
    if not (dm_ok and tm_ok):
        checks.append({"name":"modes", "status":"FAIL", "msg": msg+" (허용: DATA(live|testnet|replay), TRADE(dry|testnet|live))"})
    else:
        # 하이브리드 권장 경고만
        if dm == "replay" and tm in ("testnet","live"):
            checks.append({"name":"modes", "status":"WARN", "msg": msg+" (replay+실주문 비권장)"})
        else:
            checks.append({"name":"modes", "status":"OK", "msg": msg})

    # 2) 바이낸스 핑
    if cfg.check_live and dm == "live":
        ok, m = _http_get(LIVE_PING, cfg.timeout_s)
        checks.append({"name":"binance.live", "status":"OK" if ok else "FAIL", "msg": m})
    if cfg.check_testnet or tm == "testnet":
        ok, m = _http_get(TEST_PING, cfg.timeout_s)
        checks.append({"name":"binance.testnet", "status":"OK" if ok else "FAIL", "msg": m})

    # 3) 키 존재(선택 — 있으면 확인)
    live_keys_needed = any(_env(k) for k in ("BINANCE_LIVE_API_KEY","BINANCE_LIVE_API_SECRET"))
    test_keys_needed = any(_env(k) for k in ("BINANCE_TEST_API_KEY","BINANCE_TEST_API_SECRET"))
    if tm == "live" or live_keys_needed:
        miss = _check_env_vars(["BINANCE_LIVE_API_KEY","BINANCE_LIVE_API_SECRET"])
        checks.append({"name":"keys.live", "status":"OK" if not miss else "FAIL", "msg": " / ".join(miss) if miss else "존재"})
    if tm == "testnet" or test_keys_needed:
        miss = _check_env_vars(["BINANCE_TEST_API_KEY","BINANCE_TEST_API_SECRET"])
        checks.append({"name":"keys.testnet", "status":"OK" if not miss else "FAIL", "msg": " / ".join(miss) if miss else "존재"})

    # 4) DB
    db_path = cfg.db_path or _env("DB_PATH","./data/ftm2.sqlite")
    ok, m = _check_db(db_path)
    checks.append({"name":"db", "status":"OK" if ok else "FAIL", "msg": f"{db_path} — {m}"})

    # 5) Discord/Sentry(선택)
    if _env("DISCORD_TOKEN") or _env("DISCORD_WEBHOOK"):
        checks.append({"name":"discord", "status":"OK", "msg":"토큰/웹훅 감지"})
    else:
        checks.append({"name":"discord", "status":"WARN", "msg":"토큰/웹훅 없음(디스코드 알림 비활성)"})
    if _env("SENTRY_DSN"):
        checks.append({"name":"sentry", "status":"OK", "msg":"DSN 감지"})
    else:
        checks.append({"name":"sentry", "status":"WARN", "msg":"Sentry 비활성"})

    # 6) Ops HTTP 포트/헬스
    st, msg = _check_port_or_healthz(int(cfg.ops_port), cfg.timeout_s)
    checks.append({"name":"ops.http", "status":st, "msg": f"port={cfg.ops_port} — {msg}"})

    # 요약
    ok = all(c["status"] in ("OK",) for c in checks)
    return {"ok": ok, "checks": checks}

def _fmt_line(c: Dict[str, Any]) -> str:
    s = c["status"]
    icon = "✅" if s=="OK" else ("⚠️" if s=="WARN" else "❌")
    return f"{icon} {c['name']:<12} | {s:<4} | {c['msg']}"

def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="FTM2 Doctor — 운영 전 점검")
    ap.add_argument("--no-live", action="store_true", help="라이브 엔드포인트 핑 생략")
    ap.add_argument("--no-testnet", action="store_true", help="테스트넷 엔드포인트 핑 생략")
    ap.add_argument("--port", type=int, default=int(os.getenv("OPS_HTTP_PORT","8080")), help="ops http 포트")
    ap.add_argument("--db", type=str, default=os.getenv("DB_PATH","./data/ftm2.sqlite"), help="DB 경로")
    args = ap.parse_args(argv)

    cfg = DoctorConfig(
        check_live=not args.no_live,
        check_testnet=not args.no_testnet,
        ops_port=args.port,
        db_path=args.db,
    )
    res = run_checks(cfg)
    print("\n=== FTM2 Doctor — 점검 결과 ===")
    for c in res["checks"]:
        print(_fmt_line(c))
    print(f"종합 상태: {'정상 ✅' if res['ok'] else '주의/실패 ❌'}")
    return 0 if res["ok"] else 2

if __name__ == "__main__":
    raise SystemExit(main())
