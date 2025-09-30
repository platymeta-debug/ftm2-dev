from __future__ import annotations

import importlib
import logging
import os
from typing import Callable, Dict, Tuple

log = logging.getLogger("ftm2.hotreload")

# 엔진 모듈과 키 매핑 (존재하면 수정)
_KEYMAP: Dict[str, Tuple[str, str]] = {
    "RISK_TARGET_PCT": ("ftm2.risk.engine", "RISK_TARGET_PCT"),
    "EXEC_SLIPPAGE_BPS": ("ftm2.trade.router", "EXEC_SLIPPAGE_BPS"),
    "REENTER_COOLDOWN_S": ("ftm2.risk.gates", "REENTER_COOLDOWN_S"),
    "IK_TWIST_GUARD": ("ftm2.risk.gates", "IK_TWIST_GUARD"),
    "IK_THICK_PCT": ("ftm2.risk.gates", "IK_THICK_PCT"),
    "SC_W_TREND": ("ftm2.signal.scoring", "W_TREND"),
    "SC_W_MR": ("ftm2.signal.scoring", "W_MR"),
    "W_IMK": ("ftm2.signal.scoring", "W_IMK"),
    "CORR_CAP_PER_SIDE": ("ftm2.risk.engine", "CORR_CAP_PER_SIDE"),
    "DAILY_MAX_LOSS_PCT": ("ftm2.risk.engine", "DAILY_MAX_LOSS_PCT"),
    "REGIME_ALIGN_MODE": ("ftm2.risk.gates", "REGIME_ALIGN_MODE"),
    "IK_TENKAN": ("ftm2.data.features_ichimoku", "IK_TENKAN"),
    "IK_KIJUN": ("ftm2.data.features_ichimoku", "IK_KIJUN"),
    "IK_SEN": ("ftm2.data.features_ichimoku", "IK_SEN"),
}


class HotReloader:
    """env_key/value를 각 모듈 상수와 os.environ에 반영"""

    def __init__(self, on_announce: Callable[[str], None] | None = None) -> None:
        self.on_announce = on_announce

    # [ANCHOR:HOT_RELOAD]
    def apply(self, env_key: str, new_value: str) -> bool:
        os.environ[env_key] = str(new_value)
        modinfo = _KEYMAP.get(env_key)
        if modinfo:
            modname, attr = modinfo
            try:
                mod = importlib.import_module(modname)
                val: object = self._auto_cast(new_value)
                setattr(mod, attr, val)
                log.info("HOTRELOAD %s.%s = %s", modname, attr, val)
            except Exception as exc:  # pragma: no cover - logging side effect only
                log.warning("HOTRELOAD.FAIL %s %s", env_key, exc)
                return False
        if callable(self.on_announce):
            self.on_announce(f"{env_key} → {new_value}")
        return True

    def _auto_cast(self, v: str):
        s = str(v).strip().lower()
        if s in ("true", "false"):
            return s == "true"
        try:
            if "." in s:
                return float(s)
            return int(s)
        except Exception:
            return v
