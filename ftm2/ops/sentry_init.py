# -*- coding: utf-8 -*-
"""
Sentry 초기화 (선택)
ENV: SENTRY_DSN, SENTRY_ENV=prod|staging|dev, SENTRY_SAMPLE_RATE(트레이스), SENTRY_PROFILE(1/0)
"""
from __future__ import annotations
import os

def init_sentry() -> None:
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=dsn,
            traces_sample_rate=float(os.getenv("SENTRY_SAMPLE_RATE", "0.05")),
            environment=os.getenv("SENTRY_ENV", "prod"),
        )
    except Exception as e:
        import logging
        logging.getLogger("ftm2.sentry").warning("sentry init failed: %s", e)
