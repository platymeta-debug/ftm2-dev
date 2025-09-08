# -*- coding: utf-8 -*-
"""Feature extraction helpers"""

# [ANCHOR:FEATURE_PIPE]
from typing import Optional, Dict
import math

SAFE_EPS = 1e-12


def _safe(v, ndash=True):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None if not ndash else "—"
    return v


def compute_features_from_state(state, symbol: str, tf: str) -> Dict:
    """
    State로부터 해당 심볼/TF의 최신 피처를 가져오거나 계산.
    반환 필드: ema, rv20, atr, ret1, rv_pr(백분위 0~1), asof
    - 값이 없으면 None (렌더에서는 '—'로 대체)
    """
    src = state.features.get(symbol, {}).get(tf, {}) if hasattr(state, "features") else {}
    ema = src.get("ema")
    rv20 = src.get("rv20")
    atr = src.get("atr")
    ret1 = src.get("ret1")
    rv_pr = src.get("rv_pr")
    asof = src.get("asof") or state.now_iso()

    # 필수값 결측 시 로그 (한번만)
    if any(v is None for v in (ema, rv20, atr, ret1, rv_pr)):
        state.log.info(f"[ANL.NA] {symbol} {tf} ema={ema} rv20={rv20} atr={atr} ret1={ret1} rv_pr={rv_pr}")

    return dict(ema=ema, rv20=rv20, atr=atr, ret1=ret1, rv_pr=rv_pr, asof=asof)
# [ANCHOR:FEATURE_PIPE] end
