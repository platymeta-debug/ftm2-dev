from __future__ import annotations

from typing import Callable, Dict


# [ANCHOR:PROFILE_APPLIER]
class RiskProfileApplier:
    def __init__(self, store, hot_reload_cb: Callable[[str, str], None]):
        self.store = store
        self.hot = hot_reload_cb

    def apply_level(self, level: int) -> Dict[str, str]:
        lv = max(1, min(10, int(level)))
        a = (lv - 1) / 9.0  # α ∈ [0,1]

        out: Dict[str, str] = {}

        def setk(k: str, v) -> None:
            key = f"env.{k}"
            val = str(v)
            self.store.set(key, val)
            self.hot(k, val)
            out[k] = val

        setk("RISK_TARGET_PCT", round(0.10 + 0.25 * a, 4))
        setk("DAILY_MAX_LOSS_PCT", round(0.015 + 0.02 * a, 4))
        setk("EXEC_SLIPPAGE_BPS", round(3 + 7 * a, 2))
        setk("REENTER_COOLDOWN_S", int(round(180 - 150 * a)))
        setk("IK_TWIST_GUARD", int(round(10 - 7 * a)))
        setk("IK_THICK_PCT", round(0.85 + 0.10 * a, 3))
        setk("CORR_CAP_PER_SIDE", round(0.80 - 0.20 * a, 2))
        setk("SC_W_TREND", round(0.45 + 0.10 * a, 3))
        setk("SC_W_MR", round(0.35 - 0.10 * a, 3))
        setk("W_IMK", round(0.30 + 0.10 * a, 3))
        setk("REGIME_ALIGN_MODE", "strict" if a < 0.2 else "soft")

        self.store.set("profile.level", str(lv))
        return out
