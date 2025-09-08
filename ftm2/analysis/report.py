"""Analysis report rendering utilities."""

from __future__ import annotations

from typing import Dict, Any


def render_brief(symbol: str, tf: str, fc: Dict[str, Any], regime: str, feats: Dict[str, Any]) -> str:
    """Render a short explanation for forecast output."""

    ex = fc.get("explain", {})
    parts = []
    if ex:
        parts.append(f"모멘텀:{ex.get('mom', 0):+.2f}")
        parts.append(f"평균회귀:{ex.get('meanrev', 0):+.2f}")
        parts.append(f"돌파:{ex.get('breakout', 0):+.2f}")
        parts.append(f"변동성:{ex.get('vol', 0):+.2f}")
        parts.append(f"레짐:{ex.get('regime', 0):+.2f}")
    meta = f"r={regime} p_up={fc.get('p_up', 0):.3f}"
    feat_snip = (
        f"ema={feats.get('ema', 0):+.5f} rv%={feats.get('rv_pr', 0):.3f} atr={feats.get('atr', 0):.2f}"
    )
    return (
        f"{symbol} [{tf}] 점수:{fc['score']:+.2f} / 방향:{fc['stance']} | {meta}\n"
        f"  - 기여도: " + " ".join(parts) + f"\n  - 특성: {feat_snip}"
    )

