# -*- coding: utf-8 -*-
"""
Reconciler: 체결 기록/슬리피지 감시/라이트 리트라이(nudge)
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Tuple, List, Any, Optional
import time
import logging

try:
    from ftm2.core.persistence import Persistence
    from ftm2.core.state import StateBus
    from ftm2.trade.router import OrderRouter
    from ftm2.discord_bot.notify import enqueue_alert
except Exception:  # pragma: no cover
    from core.persistence import Persistence            # type: ignore
    from core.state import StateBus                     # type: ignore
    from trade.router import OrderRouter                # type: ignore
    from discord_bot.notify import enqueue_alert        # type: ignore

log = logging.getLogger("ftm2.recon")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@dataclass
class ProtectConfig:
    slip_warn_pct: float = 0.003   # 0.3%
    slip_max_pct: float = 0.008   # 0.8%
    stale_rel: float = 0.5        # |delta| > |target|*0.5
    stale_secs: float = 20.0      # 마지막 송신 후 20초 지나도 미이행이면 넛지
    # ε-잔차 리포트 & 부분체결 타임아웃
    eps_rel: float = 0.10         # |ε| > |target|*eps_rel 이면 리포트
    eps_abs: float = 0.0001       # 혹은 |ε| > eps_abs
    partial_timeout_s: float = 45.0  # NEW/PARTIALLY_FILLED 오래 지속 시 취소
    cancel_on_stale: bool = True  # 타임아웃 시 취소 수행



# [ANCHOR:RECONCILE]
class Reconciler:
    def __init__(self, bus: StateBus, db: Persistence, router: OrderRouter, cfg: ProtectConfig = ProtectConfig()) -> None:
        self.bus = bus
        self.db = db
        self.router = router
        self.cfg = cfg
        # 주문 상태 트래커: orderId -> 정보
        self._orders: Dict[str, Dict[str, Any]] = {}


    def _save_fill(self, rec: Dict[str, Any]) -> None:
        # qty 부호(매수 +, 매도 -)
        side = (rec.get("side") or "").upper()
        lqty = float(rec.get("lastQty") or rec.get("cumQty") or 0.0)
        qty_signed = lqty if side == "BUY" else -lqty if side == "SELL" else lqty
        px = float(rec.get("lastPrice") or rec.get("avgPrice") or 0.0)

        self.db.save_trade({
            "ts": int(rec.get("ts") or time.time()*1000),
            "symbol": rec.get("symbol"),
            "side": side,
            "qty": qty_signed,
            "px": px,
            "type": "FILL",
            "fee": float(rec.get("commission") or 0.0),
            "order_id": str(rec.get("orderId") or ""),
            "client_order_id": rec.get("clientOrderId"),
            "link_id": None,
        })

    def _slip_check(self, rec: Dict[str, Any], snapshot: Dict[str, Any]) -> Optional[str]:
        sym = rec.get("symbol")
        mark = float((snapshot.get("marks") or {}).get(sym, {}).get("price") or 0.0)
        fill_px = float(rec.get("lastPrice") or rec.get("avgPrice") or 0.0)
        if mark <= 0.0 or fill_px <= 0.0:
            return None
        slip = abs(fill_px - mark) / mark
        if slip >= self.cfg.slip_warn_pct:
            level = "최대" if slip >= self.cfg.slip_max_pct else "경고"
            txt = f"⚠️ 슬리피지 {level}: {sym} fill={fill_px:.2f} / mark={mark:.2f} ({slip*100:.2f}%)"
            log.warning("[RECON][SLIP] %s", txt)
            try:
                enqueue_alert(txt, intent="logs")
            except Exception:
                pass
            return txt
        return None

    def _maybe_nudge(self, snapshot: Dict[str, Any]) -> List[str]:
        """
        타깃-포지션 차이가 큰데 오랫동안 미이행이면 라우터 쿨다운을 해제(즉시 재시도 허용).
        """
        out: List[str] = []
        targets = snapshot.get("targets") or {}
        positions = snapshot.get("positions") or {}
        now_ms = int(snapshot.get("now_ts") or time.time()*1000)

        for sym, tgt in targets.items():
            target_qty = float(tgt.get("target_qty") or 0.0)
            pos = float((positions.get(sym) or {}).get("pa") or 0.0)
            delta = target_qty - pos
            # 상대 기준
            if abs(delta) <= max(0.0, abs(target_qty) * self.cfg.stale_rel):
                continue
            last = self.router.last_sent_ms(sym) or 0
            if (now_ms - last) / 1000.0 < self.cfg.stale_secs:
                continue
            # 넛지!
            self.router.nudge(sym)
            msg = f"🔁 넛지: {sym} Δ={delta:.6f} (target={target_qty:.6f}, pos={pos:.6f}) — 라우터 쿨다운 해제"
            log.info("[RECON][NUDGE] %s", msg)
            try:
                enqueue_alert(msg, intent="logs")
            except Exception:
                pass
            out.append(sym)
        return out


    def _track_order(self, rec: Dict[str, Any]) -> None:
        """ORDER_TRADE_UPDATE로부터 오더 상태 업데이트"""
        oid = str(rec.get("orderId") or "")
        if not oid:
            return
        status = (rec.get("status") or "").upper()
        d = self._orders.get(oid, {
            "symbol": rec.get("symbol"),
            "side": (rec.get("side") or "").upper(),
            "cumQty": 0.0,
            "status": "NEW",
            "last_ts": int(rec.get("ts") or time.time()*1000),
        })
        d["status"] = status or d["status"]
        d["cumQty"] = float(rec.get("cumQty") or d["cumQty"])
        d["last_ts"] = int(rec.get("ts") or d["last_ts"])
        self._orders[oid] = d

        if d["status"] in ("FILLED", "CANCELED", "EXPIRED", "REJECTED"):
            self._orders.pop(oid, None)

    def _epsilon_report(self, snapshot: Dict[str, Any]) -> List[str]:
        msgs: List[str] = []
        targets = snapshot.get("targets") or {}
        positions = snapshot.get("positions") or {}
        for sym, tgt in targets.items():
            target_qty = float(tgt.get("target_qty") or 0.0)
            pos = float((positions.get(sym) or {}).get("pa") or 0.0)
            eps = target_qty - pos
            thr = max(self.cfg.eps_abs, abs(target_qty) * self.cfg.eps_rel)
            if abs(eps) > thr:
                msg = f"Σε {sym}: ε={eps:.6f} (tgt={target_qty:.6f}, pos={pos:.6f})"
                log.info("[RECON][EPS] %s", msg)
                try:
                    enqueue_alert(f"📏 잔차 보고 — {msg}", intent="logs")
                except Exception:
                    pass
                msgs.append(msg)
        return msgs

    def _timeout_cancel(self, now_ms: int) -> List[str]:
        if not self.cfg.cancel_on_stale:
            return []
        kicked: List[str] = []
        for oid, d in list(self._orders.items()):
            st = (d.get("status") or "").upper()
            if st not in ("NEW", "PARTIALLY_FILLED"):
                continue
            last = int(d.get("last_ts") or 0)
            if (now_ms - last) / 1000.0 < self.cfg.partial_timeout_s:
                continue
            sym = d.get("symbol")
            r = self.router.cancel_open_orders(sym, order_id=oid)
            if r.get("ok"):
                log.warning("[RECON][CANCEL] timeout %ss %s oid=%s", self.cfg.partial_timeout_s, sym, oid)
                try:
                    enqueue_alert(f"🧹 부분체결 타임아웃 — {sym} 주문 취소(oid={oid})", intent="logs")
                except Exception:
                    pass
                kicked.append(oid)
                self._orders.pop(oid, None)
            else:
                log.warning("[RECON][CANCEL] 실패 %s oid=%s err=%s", sym, oid, r.get("error"))
        return kicked


    def process(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        fills = self.bus.drain_fills(200)
        slip_msgs: List[str] = []
        for f in fills:
            try:
                self._save_fill(f)
                log.info("[RECON][FILL] %s %s l=%s L=%s Z=%s ap=%s",
                         f.get("symbol"), f.get("side"), f.get("lastQty"), f.get("lastPrice"), f.get("cumQty"), f.get("avgPrice"))
            except Exception as e:
                log.warning("[RECON] save_fill 실패: %s", e)
            m = self._slip_check(f, snapshot)

            if m:
                slip_msgs.append(m)
            # 주문 상태 추적
            self._track_order(f)

        nudged = self._maybe_nudge(snapshot)
        eps_msgs = self._epsilon_report(snapshot)
        kicked = self._timeout_cancel(int(snapshot.get("now_ts") or time.time()*1000))
        return {
            "fills_saved": len(fills),
            "slip_warns": slip_msgs,
            "nudges": nudged,
            "eps_reports": eps_msgs,
            "timeouts": kicked,
        }
