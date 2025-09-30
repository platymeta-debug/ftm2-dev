"""Database management utilities for the trading bot.

This module encapsulates all SQLite interactions to provide a
single place to create and update order records.  It is designed to
be imported by the Discord bot main module when recording order
intentions and syncing state with Binance responses.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Tuple

DB_NAME = "trading_bot.db"


def get_db_connection() -> sqlite3.Connection:
    """Create and return a new database connection."""

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialise the database and ensure the ``orders`` table exists."""

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_order_id TEXT NOT NULL UNIQUE,
            binance_order_id INTEGER,
            symbol TEXT NOT NULL,
            order_type TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            price REAL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()
    print("데이터베이스가 성공적으로 초기화되었습니다.")


def create_order_record(order_params: Dict[str, Any]) -> Tuple[int, str]:
    """Persist a new order intent with a ``CREATED`` status."""

    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.utcnow().isoformat()
    client_order_id = f"bot_{int(datetime.utcnow().timestamp() * 1000)}"

    cursor.execute(
        """
        INSERT INTO orders (
            client_order_id,
            symbol,
            order_type,
            side,
            quantity,
            price,
            status,
            created_at,
            updated_at
        )
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            client_order_id,
            order_params.get("symbol"),
            order_params.get("type"),
            order_params.get("side"),
            order_params.get("quantity"),
            order_params.get("price"),
            "CREATED",
            now,
            now,
        ),
    )

    local_order_id = cursor.lastrowid
    conn.commit()
    conn.close()

    print(
        "주문 기록 생성됨 (ID: %s): %s %s %s"
        % (
            local_order_id,
            order_params.get("symbol"),
            order_params.get("side"),
            order_params.get("quantity"),
        )
    )
    return local_order_id, client_order_id


def update_order_from_binance(local_order_id: int, binance_response: Dict[str, Any]) -> None:
    """Update an order using the Binance API response."""

    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.utcnow().isoformat()

    cursor.execute(
        """
        UPDATE orders
        SET binance_order_id = ?, status = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            binance_response.get("orderId"),
            binance_response.get("status"),
            now,
            local_order_id,
        ),
    )
    conn.commit()
    conn.close()
    print(
        "주문 상태 업데이트됨 (ID: %s): 상태 -> %s"
        % (local_order_id, binance_response.get("status"))
    )


def update_order_status(local_order_id: int, status: str) -> None:
    """Directly update an order status (e.g. ``REJECTED``)."""

    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.utcnow().isoformat()

    cursor.execute(
        "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
        (status, now, local_order_id),
    )
    conn.commit()
    conn.close()
    print(f"주문 상태 업데이트됨 (ID: {local_order_id}): 상태 -> {status}")


def get_unsettled_orders() -> List[Dict[str, Any]]:
    """Return all orders that have not reached a final state."""

    conn = get_db_connection()
    cursor = conn.cursor()

    final_statuses = ("FILLED", "CANCELED", "REJECTED", "EXPIRED")
    query = f"SELECT * FROM orders WHERE status NOT IN {final_statuses}"

    cursor.execute(query)
    orders = [dict(row) for row in cursor.fetchall()]
    conn.close()

    print(f"미체결 주문 {len(orders)}건 조회됨.")
    return orders

