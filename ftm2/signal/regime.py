# -*- coding: utf-8 -*-
"""
Regime Classifier
- 기준: EMA 스프레드(12/26), RV 백분위(pr_rv20)
- 히스테리시스: on/off 임계 분리
- 최소 지속 바 수: 잦은 전환 억제
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple, List, Any, Optional
import logging
import os

log = logging.getLogger("ftm2.regime")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# [ANCHOR:REGIME_LOG] begin
_reg_count: Dict[str, int] = {}


def _should_log_regime(i: int, total: int) -> bool:
    mode = os.getenv("REGIME_LOG_MODE", "sample").lower()
    if mode == "off":
        return False
    if mode == "all":
        return True
    step = max(1, int(os.getenv("REGIME_LOG_SAMPLE_N", "50")))
    return i == 0 or i == total - 1 or (i % step == 0)
# [ANCHOR:REGIME_LOG] end


@dataclass
class RegimeConfig:
    # EMA 스프레드( (ema_fast-ema_slow)/ema_slow )
    ema_up_on: float = +0.0010   # 상승 추세 진입
    ema_up_off: float = +0.0005  # 상승 유지 종료(되돌림 임계)
    ema_dn_on: float = -0.0010   # 하락 추세 진입
    ema_dn_off: float = -0.0005  # 하락 유지 종료

    # RV(pr_rv20) 백분위 기반 변동성 상태
    rv_hi_on: float = 0.70
    rv_hi_off: float = 0.60
    rv_lo_on: float = 0.30
    rv_lo_off: float = 0.40

    # 전환 빈도 억제
    min_age_bars: int = 3  # 레짐 변경 후 최소 바 수 유지

    # 레이블(현지화)
    label_trend_up: str = "추세상승"
    label_trend_dn: str = "추세하락"
    label_range_hi: str = "횡보(고변동)"
    label_range_lo: str = "횡보(저변동)"


class RegimeClassifier:
    """
    features[(sym,itv)] 에서
      - ema_fast, ema_slow, pr_rv20 (fallback: rv20->중위 근처 가정)
    을 사용한다.
    """
    def __init__(self, symbols: List[str], interval: str, cfg: RegimeConfig = RegimeConfig()) -> None:
        self.symbols = symbols
        self.interval = interval
        self.cfg = cfg
        # 상태 메모리
        self._lastT: Dict[str, int] = {}             # 심볼 기준 last closed T
        self._age: Dict[str, int] = {}               # 현 레짐 유지 바 수
        self._rv_flag_hi: Dict[str, bool] = {}       # RV high 상태(히스테리시스)
        self._rv_flag_lo: Dict[str, bool] = {}       # RV low 상태(히스테리시스)
        self._trend: Dict[str, str] = {}             # "UP" / "DN" / "NONE"
        self._regime_code: Dict[str, str] = {}       # "TREND_UP","TREND_DOWN","RANGE_HIGH","RANGE_LOW"

    def _hysteresis_flag(self, sym: str, rv_pr: float) -> Tuple[bool, bool]:
        """rv_hi/rv_lo의 히스테리시스 플래그 업데이트"""
        hi = self._rv_flag_hi.get(sym, False)
        lo = self._rv_flag_lo.get(sym, False)

        # high-vol
        if not hi and rv_pr >= self.cfg.rv_hi_on:
            hi = True
        elif hi and rv_pr <= self.cfg.rv_hi_off:
            hi = False

        # low-vol
        if not lo and rv_pr <= self.cfg.rv_lo_on:
            lo = True
        elif lo and rv_pr >= self.cfg.rv_lo_off:
            lo = False

        self._rv_flag_hi[sym] = hi
        self._rv_flag_lo[sym] = lo
        return hi, lo

    def _hysteresis_trend(self, sym: str, ema_spread: float) -> str:
        """EMA 스프레드 기반 추세 방향 플래그 업데이트"""
        prev = self._trend.get(sym, "NONE")
        cur = prev

        if prev in ("NONE", "DN"):
            if ema_spread >= self.cfg.ema_up_on:
                cur = "UP"
        if prev == "UP":
            if ema_spread <= self.cfg.ema_up_off:
                cur = "NONE" if ema_spread > self.cfg.ema_dn_on else "DN"

        if prev in ("NONE", "UP"):
            if ema_spread <= self.cfg.ema_dn_on:
                cur = "DN"
        if prev == "DN":
            if ema_spread >= self.cfg.ema_dn_off:
                cur = "NONE" if ema_spread < self.cfg.ema_up_on else "UP"

        self._trend[sym] = cur
        return cur

    def _decide_regime(self, sym: str, ema_spread: float, rv_pr: float) -> Tuple[str, str]:
        """
        최종 레짐 코드/라벨 결정
        - 추세(UP/DN)가 우선. 추세가 NONE이면 변동성 플래그로 RANGE_LOW/HIGH
        """
        trend = self._hysteresis_trend(sym, ema_spread)
        rv_hi, rv_lo = self._hysteresis_flag(sym, rv_pr)

        if trend == "UP":
            return "TREND_UP", self.cfg.label_trend_up
        if trend == "DN":
            return "TREND_DOWN", self.cfg.label_trend_dn

        if rv_hi and not rv_lo:
            return "RANGE_HIGH", self.cfg.label_range_hi
        # 동시 참은 드물지만, hi가 우선
        if rv_lo:
            return "RANGE_LOW", self.cfg.label_range_lo

        # 중립 구간에서는 이전 상태 유지(있다면), 없으면 저변동 쪽으로 준수
        prev = self._regime_code.get(sym)
        if prev:
            code = prev
            label = {
                "TREND_UP": self.cfg.label_trend_up,
                "TREND_DOWN": self.cfg.label_trend_dn,
                "RANGE_HIGH": self.cfg.label_range_hi,
                "RANGE_LOW": self.cfg.label_range_lo,
            }.get(prev, self.cfg.label_range_lo)
            return code, label
        return "RANGE_LOW", self.cfg.label_range_lo

    def process_snapshot(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        features dict를 읽어 레짐 계산.
        변경(코드가 바뀜)인 경우에만 결과를 반환한다.
        """
        out: List[Dict[str, Any]] = []
        feats_map: Dict[Tuple[str, str], Dict[str, Any]] = snapshot.get("features", {})
        for sym in self.symbols:
            feats = feats_map.get((sym, self.interval))
            if not feats:
                continue
            T = int(snapshot.get("now_ts") or 0)  # 피처에 T를 전달하지 않았다면 now 기반
            # 가능하면 features 생성 시점의 T 사용
            T = int(feats.get("T") or T)

            # 최소 바 지속 처리
            lastT = self._lastT.get(sym)
            if lastT is None or T > lastT:
                self._age[sym] = self._age.get(sym, 0) + 1
                self._lastT[sym] = T

            ema_f = float(feats.get("ema_fast", 0.0))
            ema_s = float(feats.get("ema_slow", 0.0)) or 1e-12
            ema_spread = (ema_f - ema_s) / (ema_s if ema_s != 0.0 else 1e-12)

            rv_pr = feats.get("pr_rv20")
            if rv_pr is None:
                # pr가 없으면 중립(0.5) 근사
                rv_pr = 0.5
            rv_pr = float(rv_pr)

            code, label = self._decide_regime(sym, ema_spread, rv_pr)

            prev_code = self._regime_code.get(sym)
            age = self._age.get(sym, 0)

            # 전환 억제: 최소 바 미만이면 강제 유지
            if prev_code is not None and code != prev_code and age < self.cfg.min_age_bars:
                # 유지
                code = prev_code
                label = {
                    "TREND_UP": self.cfg.label_trend_up,
                    "TREND_DOWN": self.cfg.label_trend_dn,
                    "RANGE_HIGH": self.cfg.label_range_hi,
                    "RANGE_LOW": self.cfg.label_range_lo,
                }.get(prev_code, self.cfg.label_range_lo)
            else:
                # 변경(또는 초기 설정) 시 age 리셋
                if code != prev_code:
                    self._age[sym] = 0

            self._regime_code[sym] = code

            regime = {
                "code": code,
                "label": label,
                "ema_spread": float(ema_spread),
                "rv_pr": float(rv_pr),
                "age": int(self._age.get(sym, 0)),
            }

            # 변경 시에만 out
            if prev_code != code:
                idx = _reg_count.get(sym, 0)
                _reg_count[sym] = idx + 1
                if _should_log_regime(idx, 10 ** 9):
                    log.info(
                        "[REGIME_CHANGE] %s %s → %s (ema=%.5f rv_pr=%.3f)",
                        sym,
                        prev_code,
                        code,
                        ema_spread,
                        rv_pr,
                    )
                out.append({"symbol": sym, "interval": self.interval, "T": T, "regime": regime})
            else:
                # tracing 로그는 낮은 레벨로
                log.debug(
                    "[REGIME] %s %s age=%d (ema=%.5f rv_pr=%.3f)",
                    sym,
                    code,
                    regime["age"],
                    ema_spread,
                    rv_pr,
                )

        return out

    # [ANCHOR:REGIME_UPDATE] begin
    def update(self, sym: str, itv: str, bus) -> None:
        feats = bus.snapshot().get("features", {}).get((sym, itv))
        if not feats:
            return
        ema_f = float(feats.get("ema_fast", 0.0))
        ema_s = float(feats.get("ema_slow", 0.0)) or 1e-12
        ema_spread = (ema_f - ema_s) / ema_s
        rv_pr = float(feats.get("pr_rv20", 0.5))
        code = "FLAT"
        if ema_spread >= 0.001:
            code = "TREND_UP"
        elif ema_spread <= -0.001:
            code = "TREND_DOWN"
        elif rv_pr >= 0.6:
            code = "RANGE_HIGH"
        elif rv_pr <= 0.4:
            code = "RANGE_LOW"
        prev = self._regime_code.get(sym)
        self._regime_code[sym] = code
        regime = {"code": code, "ema": float(ema_spread), "rv_pr": rv_pr, "ts": int(feats.get("ts") or 0)}
        bus.update_regime(sym, itv, regime)
        if prev != code:
            idx = _reg_count.get(sym, 0)
            _reg_count[sym] = idx + 1
            if _should_log_regime(idx, 10 ** 9):
                log.info(
                    "[REGIME_CHANGE] %s %s → %s (ema=%.5f rv_pr=%.3f)",
                    sym,
                    prev,
                    code,
                    ema_spread,
                    rv_pr,
                )
    # [ANCHOR:REGIME_UPDATE] end


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except Exception:
        return default


TH_UP_SPREAD = _env_float("REG_EMA_SPREAD_UP", +0.0015)
TH_DN_SPREAD = _env_float("REG_EMA_SPREAD_DN", -0.0015)
TH_UP_SLOPE = _env_float("REG_EMA_SLOPE_UP", +0.0004)
TH_DN_SLOPE = _env_float("REG_EMA_SLOPE_DN", -0.0004)
TH_RV_HI = _env_float("REG_RV_PR_HI", 0.60)
TH_RV_LO = _env_float("REG_RV_PR_LO", 0.40)
HYST = _env_float("REG_HYST", 0.25)


def _split_prev(prev: Optional[str]) -> Tuple[str, str]:
    if not prev:
        return ("FLAT", "LOW")
    if ";" in prev:
        trend, vol = prev.split(";", 1)
    else:
        trend, vol = prev, "LOW"
    trend = trend.replace("TREND_", "").upper()
    vol = vol.replace("VOL_", "").upper()
    return (trend or "FLAT", vol or "LOW")


class Regime:
    """Lightweight 4h regime classifier used by scoring/report pipeline."""

    def __init__(self) -> None:
        self.last: Dict[str, str] = {}

    # [ANCHOR:REGIME]
    def classify(self, features_4h: Dict[str, Any], prev: Optional[str] = None) -> Dict[str, Any]:
        if not features_4h:
            return {
                "code": "TREND_FLAT;VOL_LOW",
                "label": "〰️🫧",
                "trend": "FLAT",
                "vol": "LOW",
                "ema_spread": 0.0,
                "ema_slope": 0.0,
                "rv_pr": 0.5,
            }

        spread = float(features_4h.get("ema_spread", 0.0))
        slope = float(features_4h.get("ema_slope", 0.0))
        rv_pr = float(features_4h.get("pr_rv20", features_4h.get("rv_pr", 0.5)))

        prev_trend, prev_vol = _split_prev(prev)

        trend = "FLAT"
        if spread > TH_UP_SPREAD or slope > TH_UP_SLOPE:
            trend = "UP"
        elif spread < TH_DN_SPREAD or slope < TH_DN_SLOPE:
            trend = "DOWN"

        hold_band = HYST * max(abs(TH_UP_SPREAD), abs(TH_DN_SPREAD), abs(TH_UP_SLOPE), abs(TH_DN_SLOPE))
        if prev_trend == "UP" and trend == "FLAT":
            if spread > TH_UP_SPREAD - hold_band or slope > TH_UP_SLOPE - hold_band:
                trend = "UP"
        if prev_trend == "DOWN" and trend == "FLAT":
            if spread < TH_DN_SPREAD + hold_band or slope < TH_DN_SLOPE + hold_band:
                trend = "DOWN"

        vol = "LOW"
        if rv_pr >= TH_RV_HI:
            vol = "HIGH"
        elif rv_pr <= TH_RV_LO:
            vol = "LOW"
        else:
            vol = prev_vol if prev_vol in {"HIGH", "LOW"} else "LOW"

        vol_band = HYST * abs(TH_RV_HI - TH_RV_LO)
        if prev_vol == "HIGH" and vol == "LOW" and rv_pr > TH_RV_HI - vol_band:
            vol = "HIGH"
        if prev_vol == "LOW" and vol == "HIGH" and rv_pr < TH_RV_LO + vol_band:
            vol = "LOW"

        code = f"TREND_{trend};VOL_{vol}"
        label = {"UP": "📈", "DOWN": "📉", "FLAT": "〰️"}[trend] + ("⚡" if vol == "HIGH" else "🫧")
        return {
            "code": code,
            "label": label,
            "trend": trend,
            "vol": vol,
            "ema_spread": spread,
            "ema_slope": slope,
            "rv_pr": rv_pr,
        }

    def update(self, sym: str, features_4h: Dict[str, Any]) -> Dict[str, Any]:
        prev = self.last.get(sym)
        regime = self.classify(features_4h, prev)
        self.last[sym] = f"{regime['trend']};{regime['vol']}"
        return regime
