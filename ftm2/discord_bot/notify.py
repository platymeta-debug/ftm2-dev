# -*- coding: utf-8 -*-
"""
간단 알림 큐 (파일 기반 JSONL)
- app 스레드에서 enqueue_alert() 호출 → 봇이 주기적으로 읽어 전송
"""
from __future__ import annotations
import os, json, time, uuid
from typing import Literal

QUEUE = os.path.join("runtime", "alerts_queue.jsonl")

# [ANCHOR:DISCORD_NOTIFY]
def enqueue_alert(text: str, *, intent: Literal["signals","logs","trades","system"]="signals") -> bool:
    try:
        os.makedirs(os.path.dirname(QUEUE), exist_ok=True)
        rec = {
            "id": str(uuid.uuid4()),
            "ts": int(time.time() * 1000),
            "intent": intent,
            "text": text,
            "channel": "alerts"
        }
        with open(QUEUE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False
