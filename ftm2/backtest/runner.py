# -*- coding: utf-8 -*-
"""
Backtest Runner
- CSV/JSON 계열 시계열을 읽어, 내부 더미 예측 + RiskEngine으로 타깃을 만들고
  단순 체결 모델(다음 바 오픈, 슬리피지/수수료 bps 적용)로 포지션/에쿼티를 시뮬레이션.
- 결과: trades.csv / equity.csv / pnl_daily.csv
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Tuple, Any, Optional
import csv, os, math, time, statistics
from collections import defaultdict, deque
import logging

# 기존 리스크 엔진을 재사용
try:
    from ftm2.trade.risk import RiskEngine, RiskConfig
except Exception:  # pragma: no cover
    from trade.risk import RiskEngine, RiskConfig  # type: ignore

log = logging.getLogger("ftm2.bt")
if not log.handlers:  # pragma: no cover - CLI 직행 시
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ----------------------------
# 설정
# ----------------------------
@dataclass
class BacktestConfig:
    input_path: str                # 단일CSV 또는 {symbol} 패턴
    symbols: List[str]             # 대상 심볼 (예: ["BTCUSDT","ETHUSDT"])
    interval: str = "1m"           # 정보용 필드(로그/출력)
    fees_bps: float = 1.8          # 왕복이 아니라, 체결 1회당 부과 bps
    slippage_bps: float = 1.0      # 시뮬 체결 슬리피지 bps
    exec_lag_bars: int = 1         # 0=같은바, 1=다음바 오픈
    equity0: float = 1000.0
    out_dir: str = "./reports"
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None


# ----------------------------
# 유틸: ATR/EMA/예측(더미)
# ----------------------------
def ema(prev: float, x: float, k: float) -> float:
    return (1.0 - k) * prev + k * x if prev is not None else x


def compute_atr14(prev_atr: Optional[float], o: float, h: float, l: float, c_prev: float) -> float:
    tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
    if prev_atr is None:
        return tr
    return (prev_atr * 13 + tr) / 14.0


def dummy_forecast(score_src: deque, strong_thr: float = 0.6) -> Tuple[str, float]:
    """
    간단 모멘텀 점수 → stance/score
    - 최근 N=12 수익률 EMA vs 0 비교
    """
    if len(score_src) < 2:
        return ("FLAT", 0.0)
    # 최근 변동률 r_t = log(c_t/c_{t-1})
    # 점수 = EMA_12(r) * 스케일 (경험적 10 배)
    k = 2 / (12 + 1)
    s = None
    for r in score_src:
        s = ema(s if s is not None else r, r, k)
    score = float(max(-1.0, min(1.0, (s or 0.0) * 10.0)))
    stance = "LONG" if score > 0 else "SHORT" if score < 0 else "FLAT"
    return (stance if abs(score) >= 0.05 else "FLAT", score if abs(score) >= 0.05 else 0.0)


# ----------------------------
# 데이터 로딩
# ----------------------------
def _load_single_csv(path: str, start_ms: Optional[int], end_ms: Optional[int]) -> Dict[str, List[dict]]:
    """
    단일 CSV: ts,symbol,interval,o,h,l,c,v
    return: sym -> list[{ts,o,h,l,c,v}]
    """
    out: Dict[str, List[dict]] = defaultdict(list)
    with open(path, "r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                ts = int(float(row["ts"]))
                if start_ms and ts < start_ms:
                    continue
                if end_ms and ts > end_ms:
                    continue
                sym = (row.get("symbol") or "").upper()
                o = float(row["o"])
                h = float(row["h"])
                l = float(row["l"])
                c = float(row["c"])
                v = float(row.get("v") or 0)
                out[sym].append({"ts": ts, "o": o, "h": h, "l": l, "c": c, "v": v})
            except Exception:
                continue
    for s in out:
        out[s].sort(key=lambda x: x["ts"])
    return out


def _load_pattern_csv(pattern: str, symbols: List[str], start_ms: Optional[int], end_ms: Optional[int]) -> Dict[str, List[dict]]:
    """
    패턴 CSV: {symbol} 포함 경로, 각 파일은 ts,o,h,l,c,v
    """
    out: Dict[str, List[dict]] = {}
    for s in symbols:
        path = pattern.replace("{symbol}", s)
        if not os.path.exists(path):
            log.warning("[BT] 파일 없음: %s", path)
            continue
        arr: List[dict] = []
        with open(path, "r", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                try:
                    ts = int(float(row["ts"]))
                    if start_ms and ts < start_ms:
                        continue
                    if end_ms and ts > end_ms:
                        continue
                    o = float(row["o"])
                    h = float(row["h"])
                    l = float(row["l"])
                    c = float(row["c"])
                    v = float(row.get("v") or 0)
                    arr.append({"ts": ts, "o": o, "h": h, "l": l, "c": c, "v": v})
                except Exception:
                    continue
        arr.sort(key=lambda x: x["ts"])
        out[s] = arr
    return out


# ----------------------------
# 러너
# ----------------------------
class BacktestRunner:
    def __init__(self, cfg: BacktestConfig) -> None:
        self.cfg = cfg
        # 상태
        self.positions: Dict[str, float] = {s: 0.0 for s in cfg.symbols}
        self.last_price: Dict[str, float] = {s: 0.0 for s in cfg.symbols}
        self.equity: float = cfg.equity0
        self.day_pnl: Dict[str, float] = defaultdict(float)  # key: YYYY-MM-DD
        self.atr: Dict[str, Optional[float]] = {s: None for s in cfg.symbols}
        self.prev_close: Dict[str, Optional[float]] = {s: None for s in cfg.symbols}
        self.ret_src: Dict[str, deque] = {s: deque(maxlen=64) for s in cfg.symbols}

        # 출력 버퍼
        self.trades: List[dict] = []
        self.curve: List[dict] = []

        # 리스크 엔진
        self.risk = RiskEngine(cfg.symbols, RiskConfig())

    # ---- 내부: 체결/수수료/슬리피지 ----
    def _apply_fill(self, ts: int, sym: str, delta_qty: float, ref_px: float) -> None:
        if delta_qty == 0.0 or ref_px <= 0.0:
            return
        slip = self.cfg.slippage_bps / 10000.0
        px = ref_px * (1.0 + slip if delta_qty > 0 else 1.0 - slip)
        fee = abs(delta_qty) * px * (self.cfg.fees_bps / 10000.0)
        self.positions[sym] += delta_qty
        self.equity -= fee
        self.trades.append({
            "ts": ts,
            "symbol": sym,
            "side": "BUY" if delta_qty > 0 else "SELL",
            "qty": delta_qty,
            "price": px,
            "fee": fee,
        })
        log.debug("[BT][trade] %s %s qty=%.6f px=%.2f fee=%.4f", sym, "BUY" if delta_qty > 0 else "SELL", delta_qty, px, fee)

    def _mark_to_market(self, ts: int, sym: str, new_px: float) -> None:
        old_px = self.last_price.get(sym) or new_px
        pnl = self.positions[sym] * (new_px - old_px)
        self.equity += pnl
        self.last_price[sym] = new_px
        day = time.strftime("%Y-%m-%d", time.gmtime(ts / 1000))
        self.day_pnl[day] += pnl

    # ---- 메인 루프 ----
    def run(self) -> Dict[str, Any]:
        # 데이터 로드
        if "{symbol}" in self.cfg.input_path:
            data = _load_pattern_csv(self.cfg.input_path, self.cfg.symbols, self.cfg.start_ms, self.cfg.end_ms)
        else:
            data = _load_single_csv(self.cfg.input_path, self.cfg.start_ms, self.cfg.end_ms)
            if not self.cfg.symbols:
                self.cfg.symbols = sorted(list(data.keys()))
        log.info("[BT] load symbols=%s", ",".join(self.cfg.symbols))

        idx: Dict[str, int] = {s: 0 for s in self.cfg.symbols}
        exec_queue: Dict[str, deque] = {s: deque() for s in self.cfg.symbols}

        while True:
            next_ts = None
            next_syms: List[str] = []
            for s in self.cfg.symbols:
                arr = data.get(s) or []
                i = idx[s]
                if i < len(arr):
                    ts = arr[i]["ts"]
                    if next_ts is None or ts < next_ts:
                        next_ts = ts
                        next_syms = [s]
                    elif ts == next_ts:
                        next_syms.append(s)
            if next_ts is None:
                break

            # 1) 체결 대기 큐 처리
            for s in self.cfg.symbols:
                q = exec_queue[s]
                while q and q[0][0] <= next_ts:
                    _, dqty, ref_px = q.popleft()
                    self._apply_fill(next_ts, s, dqty, ref_px)

            # 2) 새 바 업데이트
            for s in next_syms:
                bar = data[s][idx[s]]
                idx[s] += 1
                ts, o, h, l, c = bar["ts"], bar["o"], bar["h"], bar["l"], bar["c"]
                self._mark_to_market(ts, s, c)

                prev_c = self.prev_close[s] if self.prev_close[s] is not None else c
                self.atr[s] = compute_atr14(self.atr[s], o, h, l, prev_c)
                self.prev_close[s] = c
                try:
                    self.ret_src[s].append(math.log(c / prev_c) if prev_c > 0 else 0.0)
                except Exception:
                    self.ret_src[s].append(0.0)

            # 3) 스냅샷 구성 및 리스크 호출
            snapshot = {
                "marks": {
                    s: {"price": (data[s][idx[s] - 1]["c"] if idx[s] > 0 and s in next_syms else self.last_price.get(s, 0.0))}
                    for s in self.cfg.symbols
                    if (data.get(s) or [])
                },
                "klines": {
                    (s, self.cfg.interval): {"x": True, "T": (data[s][idx[s] - 1]["ts"] if idx[s] > 0 else next_ts)}
                    for s in next_syms
                },
                "features": {
                    (s, self.cfg.interval): {"atr14": (self.atr[s] or 0.0), "c": (data[s][idx[s] - 1]["c"] if idx[s] > 0 else 0.0)}
                    for s in self.cfg.symbols
                },
                "forecasts": {},
                "account": {"totalWalletBalance": f"{self.equity:.8f}"},
                "positions": {s: {"pa": self.positions[s], "ep": 0.0, "up": 0.0} for s in self.cfg.symbols},
                "risk": {"day_pnl_pct": 0.0},
            }

            for s in self.cfg.symbols:
                stance, score = dummy_forecast(self.ret_src[s])
                snapshot["forecasts"][(s, self.cfg.interval)] = {"stance": stance, "score": score}

            targets = self.risk.process_snapshot(snapshot)

            # 4) 타깃 → 델타 → 체결 예약
            for t in targets:
                s = t["symbol"]
                tgt_qty = float(t.get("target_qty") or 0.0)
                cur = self.positions[s]
                delta = tgt_qty - cur
                if abs(delta) < 1e-12:
                    continue
                arr = data.get(s) or []
                i = idx[s]
                if i >= len(arr):
                    ref_px = snapshot["marks"][s]["price"] or 0.0
                    exec_queue[s].append((next_ts, delta, ref_px))
                else:
                    ref_px = arr[i]["o"]
                    eta_ts = arr[i]["ts"] if self.cfg.exec_lag_bars >= 1 else next_ts
                    exec_queue[s].append((eta_ts, delta, ref_px))

            # 5) 에쿼티 곡선 기록
            tot_notional = sum(abs(self.positions[s]) * (self.last_price.get(s) or 0.0) for s in self.cfg.symbols)
            self.curve.append({"ts": next_ts, "equity": self.equity, "notional": tot_notional})

        # 잔여 큐 처리
        for s in self.cfg.symbols:
            q = exec_queue[s]
            while q:
                ts_exec, dqty, ref_px = q.popleft()
                self._apply_fill(ts_exec, s, dqty, ref_px)

        # 결과 저장
        os.makedirs(self.cfg.out_dir, exist_ok=True)
        trades_csv = os.path.join(self.cfg.out_dir, f"bt_trades_{self.cfg.interval}.csv")
        equity_csv = os.path.join(self.cfg.out_dir, f"bt_equity_{self.cfg.interval}.csv")
        pnl_csv = os.path.join(self.cfg.out_dir, f"bt_pnl_daily_{self.cfg.interval}.csv")

        with open(trades_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["ts", "symbol", "side", "qty", "price", "fee"])
            w.writeheader()
            w.writerows(self.trades)

        with open(equity_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["ts", "equity", "notional"])
            w.writeheader()
            w.writerows(self.curve)

        with open(pnl_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["date", "pnl"])
            w.writeheader()
            for d, v in sorted(self.day_pnl.items()):
                w.writerow({"date": d, "pnl": v})

        # 요약 지표
        eq0 = self.cfg.equity0
        eq1 = self.equity
        ret = (eq1 / eq0 - 1.0) if eq0 > 0 else 0.0
        peak = -1e18
        maxdd = 0.0
        for p in self.curve:
            e = p["equity"]
            peak = max(peak, e)
            dd = 0.0 if peak <= 0 else (peak - e) / peak
            maxdd = max(maxdd, dd)
        daily = [v / eq0 for v in self.day_pnl.values()]
        sharpe = 0.0
        if len(daily) >= 2:
            try:
                sharpe = (statistics.mean(daily) / (statistics.pstdev(daily) + 1e-9)) * (252 ** 0.5)
            except Exception:
                sharpe = 0.0

        summ = {
            "symbols": self.cfg.symbols,
            "interval": self.cfg.interval,
            "equity0": eq0,
            "equity1": eq1,
            "ret_total": ret,
            "max_dd": maxdd,
            "sharpe_like": sharpe,
            "n_trades": len(self.trades),
        }
        log.info(
            "[BT] done eq0=%.2f eq1=%.2f ret=%.2f%% maxDD=%.2f%% trades=%d",
            eq0,
            eq1,
            ret * 100.0,
            maxdd * 100.0,
            len(self.trades),
        )
        return {
            "summary": summ,
            "trades_csv": trades_csv,
            "equity_csv": equity_csv,
            "pnl_daily_csv": pnl_csv,
        }

