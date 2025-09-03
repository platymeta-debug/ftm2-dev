# -*- coding: utf-8 -*-
"""
로테이션 파일 로그 + 콘솔 로그 구성자
ENV:
  LOG_LEVEL, LOG_DIR, LOG_FILE, LOG_MAX_BYTES, LOG_BACKUPS
"""
from __future__ import annotations
import os, logging, logging.handlers

def setup_logging() -> None:
    lvl = os.getenv("LOG_LEVEL", "INFO").upper()
    log_dir = os.getenv("LOG_DIR", "/var/log/ftm2")
    log_file = os.getenv("LOG_FILE", "app.log")
    max_bytes = int(float(os.getenv("LOG_MAX_BYTES", "10485760")))
    backups = int(float(os.getenv("LOG_BACKUPS", "5")))

    os.makedirs(log_dir, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(getattr(logging, lvl, logging.INFO))

    # 콘솔
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # 파일 로테이션
    try:
        fh = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, log_file), maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception as e:
        root.warning("[LOG] file handler init failed: %s", e)
