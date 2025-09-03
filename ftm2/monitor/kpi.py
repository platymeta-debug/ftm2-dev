# -*- coding: utf-8 -*-
"""
KPI Reporter
- 전략/리스크/실행 품질을 한눈에 보는 요약(텍스트 패널)
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, Tuple, List, Optional
import time, math, logging

log = logging.getLogger("ftm2.kpi")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

@dataclass
class KPIConfig:
    enabled: bool = True
    report_sec: int = 30
    to_discord: bool = True
    only_on_change: bool = True   # KPI 핵심 수치가 바뀔 때만 전송

# [ANCHOR:KPI]
class KPIReporter:
    def __init__(self, cfg: KPIConfig = KPIConfig()) -> None:
        self.cfg = cfg
        self._last_post_ms = 0
        self._last_fingerprint: Optional[str] = None

    # --- helpers ---
    @staticmethod
    def _fmt_pct(x: float, digits: int = 2) -> str:
        return f"{x*100:.{digits}f}%"

    @staticmethod
    def _safe(d: Dict[str, Any], *keys, default=None):
        cur = d
        for k in keys:
            if not isinstance(cur, dict): return default
            cur = cur.get(k)
            if cur is None: return default
        return cur

    def _regime_counts(self, snapshot: Dict[str, Any]) -> Dict[str, int]:
        regs = snapshot.get("regimes") or {}
        cnt = {"TREND_UP":0, "TREND_DOWN":0, "RANGE_HIGH":0, "RANGE_LOW":0}
        for (_, _), r in regs.items():
            c = (r.get("code") or "")
            if c in cnt: cnt[c]+=1
        return cnt

    def _forecast_stats(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        fcs = snapshot.get("forecasts") or {}
        n = len(fcs)
        if n == 0: return {"n":0}
        ssum=0.0; long=short=flat=strong=0
        for (_, _), fc in fcs.items():
            s = float(fc.get("score") or 0.0); ssum += s
            st = (fc.get("stance") or "FLAT").upper()
            if st=="LONG": long+=1
            elif st=="SHORT": short+=1
            else: flat+=1
            if abs(s) >= 0.60: strong += 1
        return {"n":n,"avg_score":(ssum/n),"long":long,"short":short,"flat":flat,"strong":strong}

    def compute(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        StateBus.snapshot() → KPI dict
        """
        now = int(snapshot.get("now_ts") or time.time()*1000)
        boot = int(snapshot.get("boot_ts") or now)
        up_s = max(0, (now - boot)//1000)

        risk = snapshot.get("risk") or {}
        equity = float(risk.get("equity") or 0.0)
        day_pnl_pct = float(risk.get("day_pnl_pct") or 0.0)
        day_cut = bool(risk.get("day_cut"))

        positions = snapshot.get("positions") or {}
        marks = snapshot.get("marks") or {}
        # 총 노셔널/레버
        notional = 0.0
        for sym, p in positions.items():
            qty = float(p.get("pa") or 0.0)
            px = float((marks.get(sym) or {}).get("price") or 0.0)
            notional += abs(qty)*px
        lever = (notional/equity) if equity>0 else 0.0

        # 익스포저(사이드별)
        used_long = float(risk.get("used_long_ratio") or 0.0)
        used_short = float(risk.get("used_short_ratio") or 0.0)

        # 레짐/예측
        reg = self._regime_counts(snapshot)
        fc  = self._forecast_stats(snapshot)

        # 실행 품질/원장(선행 티켓에서 guard.exec_quality / guard.exec_ledger에 투영됨)
        g = snapshot.get("guard") or {}
        eq = g.get("exec_quality") or {}
        ol = g.get("exec_ledger") or {}

        # 미체결 수
        oo = snapshot.get("open_orders") or {}
        open_cnt = sum(len(v) for v in oo.values())

        kpi = {
            "uptime_s": up_s,
            "equity": equity,
            "lever": lever,
            "day_pnl_pct": day_pnl_pct,
            "day_cut": day_cut,
            "used_long": used_long,
            "used_short": used_short,
            "regimes": reg,
            "forecast": fc,
            "exec_quality": {
                "samples": int(eq.get("samples") or 0),
                "avg_bps": float((eq.get("slip_bps_overall") or {}).get("avg") or 0.0),
                "p90_bps": float((eq.get("slip_bps_overall") or {}).get("p90") or 0.0),
                "nudges": int(eq.get("nudges") or 0),
                "cancels": int(eq.get("cancels") or 0),
            },
            "order_ledger": {
                "orders": int(ol.get("orders") or 0),
                "fill_rate": float(ol.get("fill_rate") or 0.0),
                "cancel_rate": float(ol.get("cancel_rate") or 0.0),
                "p50_ttf_ms": float(ol.get("p50_ttf_ms") or 0.0),
            },
            "open_orders": open_cnt,
            "ts": now,
        }
        log.debug("[KPI] compute %s", kpi)
        return kpi

    # 간단한 변화 지문(fingerprint) — 너무 민감하지 않게 핵심만
    def _fingerprint(self, kpi: Dict[str, Any]) -> str:
        f = (
            round(kpi["equity"], 2),
            round(kpi["lever"], 3),
            round(kpi["day_pnl_pct"], 2),
            kpi["day_cut"],
            kpi["forecast"].get("strong",0),
            kpi["exec_quality"]["p90_bps"],
            kpi["order_ledger"]["fill_rate"],
            kpi["open_orders"],
        )
        return str(f)

    def should_post(self, kpi: Dict[str, Any]) -> bool:
        if not self.cfg.only_on_change:
            return True
        fp = self._fingerprint(kpi)
        if fp != self._last_fingerprint:
            self._last_fingerprint = fp
            return True
        return False

    def format_text(self, kpi: Dict[str, Any]) -> str:
        # 한국어 대시보드 텍스트(한눈에)
        up_min = kpi["uptime_s"] // 60
        reg = kpi["regimes"]; fc = kpi["forecast"]; eq = kpi["exec_quality"]; ol = kpi["order_ledger"]
        bar = "─"*34
        return (
 f"""📊 **FTM2 KPI 대시보드**
{bar}
⏱️ 가동시간: **{up_min}분**
💰 자본(Equity): **{kpi['equity']:.2f}**  레버리지: **{kpi['lever']:.2f}x**
📉 당일손익: **{kpi['day_pnl_pct']:.2f}%**  {'🛑 데일리컷 발동' if kpi['day_cut'] else '✅ 정상'}

📐 익스포저: 롱 {self._fmt_pct(kpi['used_long'],1)} / 숏 {self._fmt_pct(kpi['used_short'],1)}
🧭 레짐: ↑{reg['TREND_UP']} ↓{reg['TREND_DOWN']} 高{reg['RANGE_HIGH']} 低{reg['RANGE_LOW']}
🎯 예측: N={fc.get('n',0)} 강신호={fc.get('strong',0)} 평균스코어={fc.get('avg_score',0.0):.2f}

⚙️ 실행 품질(최근): 샘플={eq['samples']}  bps(avg={eq['avg_bps']:.2f}, p90={eq['p90_bps']:.2f})  넛지={eq['nudges']}  취소={eq['cancels']}
🧾 주문원장(최근): 주문={ol['orders']}  체결률={ol['fill_rate']*100:.1f}%  TTF(p50)={ol['p50_ttf_ms']:.0f}ms
📮 미체결 주문: {kpi['open_orders']} 건
{bar}"""
        )
