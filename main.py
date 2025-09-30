"""Discord trading bot orchestrator wiring strategies and the trading engine."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any, Dict

import discord
from binance.client import Client
from binance.exceptions import BinanceAPIException
from discord.ext import commands, tasks
from dotenv import load_dotenv

import database_manager as db
from strategies.example_strategy import SimpleRSIStrategy
from trading_engine import TradingEngine

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
IS_TESTNET = os.getenv("IS_TESTNET", "true").lower() == "true"

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

try:
    client = Client(BINANCE_API_KEY, BINANCE_API_SECRET, testnet=IS_TESTNET)
    client.futures_ping()
    print(f"바이낸스 연결 성공. (환경: {'테스트넷' if IS_TESTNET else '실거래'})")
except Exception as exc:  # pragma: no cover - network interaction
    print(f"바이낸스 연결 실패: {exc}")
    raise SystemExit(1)

trading_engine = TradingEngine(client)

strategy_settings = {
    "timeframe": "1m",
    "rsi_period": 14,
    "rsi_oversold": 30,
    "rsi_overbought": 70,
    "quantity": 0.001,
}
active_strategy = SimpleRSIStrategy(symbol="BTCUSDT", settings=strategy_settings)


async def sync_orders_on_startup() -> None:
    """Synchronise local orders with Binance on bot startup."""

    print("시작 시 주문 상태 동기화를 시작합니다...")
    unsettled_orders = db.get_unsettled_orders()

    if not unsettled_orders:
        print("동기화할 미체결 주문이 없습니다.")
        return

    for order in unsettled_orders:
        try:
            if order["binance_order_id"]:
                server_order = client.futures_get_order(
                    symbol=order["symbol"],
                    orderId=order["binance_order_id"],
                )
            else:
                server_order = client.futures_get_order(
                    symbol=order["symbol"],
                    origClientOrderId=order["client_order_id"],
                )

            if order["status"] != server_order["status"]:
                print(
                    "상태 불일치 발견 (ID: {id}). 로컬: {local}, 서버: {server}. 동기화합니다.".format(
                        id=order["id"],
                        local=order["status"],
                        server=server_order["status"],
                    )
                )
                db.update_order_from_binance(order["id"], server_order)

        except BinanceAPIException as exc:
            if exc.code == -2013:
                print(
                    f"주문 ID {order['id']}를 서버에서 찾을 수 없습니다. 상태를 'CANCELED'로 간주합니다."
                )
                db.update_order_status(order["id"], "CANCELED")
            else:
                print(f"주문 동기화 중 오류 발생 (ID: {order['id']}): {exc}")
        except Exception as exc:  # pragma: no cover - defensive logging
            print(f"알 수 없는 오류로 주문 동기화 실패 (ID: {order['id']}): {exc}")

        await asyncio.sleep(0.2)

    print("주문 상태 동기화가 완료되었습니다.")


@tasks.loop(minutes=5)
async def periodic_sync_task() -> None:
    """Periodically refresh unsettled orders with Binance state."""

    print(f"[{datetime.utcnow().isoformat()}] 주기적 주문 동기화를 수행합니다...")
    await sync_orders_on_startup()


@tasks.loop(minutes=1)
async def strategy_check_loop() -> None:
    """Evaluate the active strategy and execute orders when signalled."""

    print(f"\n[{datetime.utcnow().isoformat()}] 전략 신호 확인 중: {active_strategy.strategy_name}...")
    order_params = active_strategy.check_signal(client)

    if order_params:
        success, result = await trading_engine.execute_order(
            order_params,
            source=active_strategy.strategy_name,
        )
        if success:
            print(
                "전략 주문 성공: 심볼={symbol}, 바이낸스 ID={order_id}".format(
                    symbol=result.get("symbol"),
                    order_id=result.get("orderId"),
                )
            )
        else:
            print(f"전략 주문 실패: {result.get('error')}")
    else:
        print("거래 신호 없음.")


@bot.event
async def on_ready() -> None:
    print(f"{bot.user.name} 봇이 준비되었습니다.")
    db.init_db()
    await sync_orders_on_startup()

    if not periodic_sync_task.is_running():
        periodic_sync_task.start()

    if not strategy_check_loop.is_running():
        strategy_check_loop.start()


@bot.command(name="주문")
@commands.is_owner()
async def place_order(
    ctx: commands.Context, symbol: str, side: str, quantity: float, price: float | None = None
) -> None:
    """Place a futures order via Binance."""

    side = side.upper()
    if side not in {"BUY", "SELL"}:
        await ctx.send("잘못된 주문 방향입니다. 'BUY' 또는 'SELL'을 사용해주세요.")
        return

    order_type = "LIMIT" if price is not None else "MARKET"

    order_params: Dict[str, Any] = {
        "symbol": symbol.upper(),
        "side": side,
        "type": order_type,
        "quantity": quantity,
    }

    if order_type == "LIMIT":
        order_params["price"] = price
        order_params["timeInForce"] = "GTC"

    await ctx.send(f"수동 주문 요청: {symbol.upper()} {side} {quantity}")
    success, result = await trading_engine.execute_order(
        order_params,
        source=f"manual_by_{ctx.author.name}",
    )

    if success:
        await ctx.send(
            "✅ 주문 성공! 바이낸스 ID: {order_id}, 상태: {status}".format(
                order_id=result.get("orderId"),
                status=result.get("status"),
            )
        )
    else:
        await ctx.send(f"❌ 주문 실패: {result.get('error')}")


if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN or not BINANCE_API_KEY or not BINANCE_API_SECRET:
        print("오류:.env 파일에 DISCORD_BOT_TOKEN, BINANCE_API_KEY, BINANCE_API_SECRET을 설정해야 합니다.")
    else:
        bot.run(DISCORD_BOT_TOKEN)
