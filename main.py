"""Discord trading bot entry point with persistent order tracking."""

from __future__ import annotations

import asyncio
import os

import discord
from binance.client import Client
from binance.exceptions import BinanceAPIException
from discord.ext import commands
from dotenv import load_dotenv

import database_manager as db


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


@bot.event
async def on_ready() -> None:
    print(f"{bot.user.name} 봇이 준비되었습니다.")
    db.init_db()
    await sync_orders_on_startup()


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

    order_params = {
        "symbol": symbol.upper(),
        "side": side,
        "type": order_type,
        "quantity": quantity,
    }

    if order_type == "LIMIT":
        order_params["price"] = price
        order_params["timeInForce"] = "GTC"

    local_order_id, client_order_id = db.create_order_record(order_params)
    await ctx.send(
        f"주문 기록 생성됨 (로컬 ID: {local_order_id}). 바이낸스에 전송을 시도합니다..."
    )

    try:
        binance_order = client.futures_create_order(
            newClientOrderId=client_order_id,
            **order_params,
        )

        db.update_order_from_binance(local_order_id, binance_order)
        await ctx.send(
            "✅ 주문 성공!\n"
            f"심볼: {binance_order['symbol']}\n"
            f"바이낸스 ID: {binance_order['orderId']}\n"
            f"상태: {binance_order['status']}"
        )

    except BinanceAPIException as exc:
        db.update_order_status(local_order_id, "REJECTED")
        await ctx.send(f"❌ 주문 실패 (API 오류): {exc}")
    except Exception as exc:  # pragma: no cover - defensive logging
        db.update_order_status(local_order_id, "REJECTED")
        await ctx.send(f"❌ 주문 실패 (알 수 없는 오류): {exc}")


if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN or not BINANCE_API_KEY or not BINANCE_API_SECRET:
        print("오류:.env 파일에 DISCORD_BOT_TOKEN, BINANCE_API_KEY, BINANCE_API_SECRET을 설정해야 합니다.")
    else:
        bot.run(DISCORD_BOT_TOKEN)

