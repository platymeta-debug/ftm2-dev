# -*- coding: utf-8 -*-
"""StateBus helpers"""

# [ANCHOR:STATEBUS_ANALYSIS]
def publish_analysis_snapshot(state, symbols: list):
    """
    심볼별 Multi-TF ScoreDetail 스냅샷을 계산해 state.monitor['analysis']에 탑재.
    """
    from ftm2.analysis.scoring import compute_multi_tf
    snap = {}
    for sym in symbols:
        try:
            snap[sym] = compute_multi_tf(state, sym)
        except Exception as e:
            state.log.warning(f"[ANL.SNAP][WARN] {sym} failed: {e}")
    state.monitor["analysis"] = snap
    return snap
# [ANCHOR:STATEBUS_ANALYSIS] end
