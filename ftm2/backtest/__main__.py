# -*- coding: utf-8 -*-
"""
CLI: python -m ftm2.backtest
ENV/DB 로더를 통해 설정을 불러와 BacktestRunner 실행
"""
from __future__ import annotations
import os, json, logging

try:
    from ftm2.core.config import load_backtest_cfg
except Exception:  # pragma: no cover
    from core.config import load_backtest_cfg  # type: ignore

try:
    from ftm2.backtest.runner import BacktestRunner, BacktestConfig
except Exception:  # pragma: no cover
    from backtest.runner import BacktestRunner, BacktestConfig  # type: ignore

log = logging.getLogger("ftm2.bt.cli")
if not log.handlers:  # pragma: no cover - direct run
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> None:
    cfgv = load_backtest_cfg(None)  # DB 미사용 시 None 허용
    syms = [s.strip().upper() for s in (cfgv.symbols or "").split(",") if s.strip()]
    bt = BacktestRunner(
        BacktestConfig(
            input_path=cfgv.input_path,
            symbols=syms,
            interval=cfgv.interval or "1m",
            fees_bps=float(cfgv.fees_bps),
            slippage_bps=float(cfgv.slippage_bps),
            exec_lag_bars=int(cfgv.exec_lag_bars),
            equity0=float(cfgv.equity0),
            out_dir=cfgv.out_dir or "./reports",
            start_ms=cfgv.start_ms,
            end_ms=cfgv.end_ms,
        )
    )
    res = bt.run()
    print(json.dumps(res["summary"], ensure_ascii=False, indent=2))
    print(f"- trades: {res['trades_csv']}\n- equity: {res['equity_csv']}\n- pnl_daily: {res['pnl_daily_csv']}")


if __name__ == "__main__":
    main()

