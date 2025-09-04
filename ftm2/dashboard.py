# -*- coding: utf-8 -*-
"""대시보드 메시지 핀 복구 및 업데이트 유틸."""
from __future__ import annotations

import os
import sqlite3
import logging
import asyncio
from typing import Dict, Any

from .db import init_db

try:  # pragma: no cover - discord 미설치 환경 대응
    import discord  # type: ignore
    from discord.errors import Forbidden  # type: ignore
except Exception:  # pragma: no cover
    discord = None  # type: ignore
    class Forbidden(Exception):
        ...

log = logging.getLogger("ftm2.dashboard")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# [ANCHOR:DASHBOARD_PIN_RECOVERY] begin
"""
대시보드 메시지를 항상 '핀 고정' 상태로 유지한다.
- 저장 위치: DB config('DASHBOARD_MSG_ID')
- 시작 시: ID가 유효/존재+핀 여부 확인→미핀 시 pin(), 없으면 새로 만들고 pin()
"""


def _db_path() -> str:
    return os.getenv("DB_PATH", "./runtime/trader.db")


def _cfg_get(conn: sqlite3.Connection, key: str, default=None):
    try:
        cur = conn.execute("SELECT val FROM config WHERE key=?", (key,))
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else default
    except sqlite3.OperationalError:
        return default


def _cfg_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO config(key, val)
        VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET val=excluded.val
        """,
        (key, value),
    )
    conn.commit()


async def ensure_dashboard_message(bot, channel_id: int, content_factory):
    """
    - content_factory(): str  — 새 메시지 본문 생성 콜백
    반환: (discord.Message, created: bool)
    """
    import discord

    with init_db(_db_path()) as conn:
        ch = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        msg_id = _cfg_get(conn, "DASHBOARD_MSG_ID")

        msg = None
        created = False

        if msg_id:
            try:
                msg = await ch.fetch_message(int(msg_id))
            except Exception:
                msg = None

        if msg is None:
            txt = content_factory()
            msg = await ch.send(txt)
            _cfg_set(conn, "DASHBOARD_MSG_ID", str(msg.id))
            created = True
            log.info("[대시보드] 신규 생성(mid=%s)", msg.id)

        allow_pin = os.getenv("DISCORD_ALLOW_PIN", "true").lower() != "false"
        if allow_pin:
            if _cfg_get(conn, "DASHBOARD_PIN_OK") == "0":
                log.warning("DASHBOARD: pin skipped (missing permission).")
            else:
                try:
                    await msg.pin(reason="FTM2 dashboard auto pin")
                    _cfg_set(conn, "DASHBOARD_PIN_OK", "1")
                except Forbidden:
                    _cfg_set(conn, "DASHBOARD_PIN_OK", "0")
                    log.warning("DASHBOARD: pin skipped (missing permission).")
                except Exception:
                    log.exception("E_DASHBOARD_PIN_RECOVER")

        return msg, created


async def update_dashboard(bot, channel_id: int, render_text: str):
    """기존 메시지 편집(update). 없으면 생성 후 핀."""
    with init_db(_db_path()) as conn:
        msg_id = _cfg_get(conn, "DASHBOARD_MSG_ID")
        ch = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        msg = None
        if msg_id:
            try:
                msg = await ch.fetch_message(int(msg_id))
            except Exception:
                msg = None
        if msg is None:
            def _factory():
                return render_text

            msg, _ = await ensure_dashboard_message(bot, channel_id, _factory)
        else:
            try:
                await msg.edit(content=render_text)
                log.info("[대시보드] 업데이트 완료")
            except Exception:
                log.exception("E_DASHBOARD_EDIT")


# [ANCHOR:DASHBOARD_PIN_RECOVERY] end


def _fmt_number(x: float) -> str:
    try:
        return f"{x:,.4f}"
    except Exception:
        return str(x)


def render_dashboard(snap: Dict[str, Any]) -> str:
    marks = snap.get("marks", {})
    positions = snap.get("positions", {})
    uptime = int((snap.get("now_ts", 0) - snap.get("boot_ts", 0)) / 1000)
    lines = [
        "📊 **실시간 대시보드**",
        f"• 가동 시간: `{uptime}s`",
    ]
    if marks:
        sym_parts = []
        for s, v in marks.items():
            sym_parts.append(f"{s}: **{_fmt_number(v.get('price', 0.0))}**")
        lines.append("• 시세(마크프라이스): " + " | ".join(sym_parts))
    if positions:
        pos_parts = []
        for s, p in positions.items():
            pos_parts.append(
                f"{s}: 수량 `{_fmt_number(p.get('pa', 0.0))}` / 진입가 `{_fmt_number(p.get('ep',0.0))}` / 평가손익 `{_fmt_number(p.get('up',0.0))}`"
            )
        lines.append("• 포지션: " + " | ".join(pos_parts))
    lines.append("\n_※ 본 메시지는 스팸 방지를 위해 **편집(update)** 방식으로 갱신됩니다._")
    return "\n".join(lines)


__all__ = [
    "ensure_dashboard_message",
    "update_dashboard",
    "render_dashboard",
]

