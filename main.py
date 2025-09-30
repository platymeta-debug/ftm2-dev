# 파일명: main.py (기존 메인 파일 수정안)

import os
import discord
from discord.ext import commands, tasks # tasks 확장을 임포트합니다.
from dotenv import load_dotenv
from binance.client import Client
from binance.exceptions import BinanceAPIException
import asyncio

# 새로 추가된 데이터베이스 매니저 임포트
import database_manager as db

# --- 초기 설정 ---
#.env 파일에서 환경 변수 로드
load_dotenv()

DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
BINANCE_API_KEY = os.getenv('BINANCE_API_KEY') # 테스트넷 또는 실거래 키
BINANCE_API_SECRET = os.getenv('BINANCE_API_SECRET') # 테스트넷 또는 실거래 시크릿
IS_TESTNET = os.getenv('IS_TESTNET', 'true').lower() == 'true' # 기본값은 테스트넷

# --- Discord 봇 설정 ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# --- Binance 클라이언트 설정 ---
try:
    client = Client(BINANCE_API_KEY, BINANCE_API_SECRET, testnet=IS_TESTNET)
    # 연결 테스트
    client.futures_ping()
    print(f"바이낸스 연결 성공. (환경: {'테스트넷' if IS_TESTNET else '실거래'})")
except Exception as e:
    print(f"바이낸스 연결 실패: {e}")
    exit()

# --- 신규 기능: 시작 시 주문 동기화 ---
async def sync_orders_on_startup():
    """봇 시작 시 로컬 DB와 바이낸스 서버의 주문 상태를 동기화합니다."""
    print("시작 시 주문 상태 동기화를 시작합니다...")
    unsettled_orders = db.get_unsettled_orders()
    
    if not unsettled_orders:
        print("동기화할 미체결 주문이 없습니다.")
        return

    for order in unsettled_orders:
        try:
            # 바이낸스에서 최신 주문 정보 조회
            if order['binance_order_id']:
                server_order = client.futures_get_order(
                    symbol=order['symbol'],
                    orderId=order['binance_order_id']
                )
            else:
                 server_order = client.futures_get_order(
                    symbol=order['symbol'],
                    origClientOrderId=order['client_order_id']
                )

            # 상태가 다를 경우, 서버의 상태를 기준으로 로컬 DB 업데이트
            if order['status']!= server_order['status']:
                print(f"상태 불일치 발견 (ID: {order['id']}). 로컬: {order['status']}, 서버: {server_order['status']}. 동기화합니다.")
                db.update_order_from_binance(order['id'], server_order)

        except BinanceAPIException as e:
            if e.code == -2013: # "Order does not exist"
                print(f"주문 ID {order['id']}를 서버에서 찾을 수 없습니다. 상태를 'CANCELED'로 간주합니다.")
                db.update_order_status(order['id'], 'CANCELED')
            else:
                print(f"주문 동기화 중 오류 발생 (ID: {order['id']}): {e}")
        except Exception as e:
            print(f"알 수 없는 오류로 주문 동기화 실패 (ID: {order['id']}): {e}")
        
        await asyncio.sleep(0.2) # API 속도 제한 방지

    print("주문 상태 동기화가 완료되었습니다.")


# --- 신규 기능: 주기적인 백그라운드 동기화 작업 ---
@tasks.loop(minutes=5) # 5분마다 이 함수를 실행합니다.
async def periodic_sync_task():
    """주기적으로 로컬 DB와 바이낸스 서버의 주문 상태를 동기화합니다."""
    print(f"[{datetime.utcnow().isoformat()}] 주기적인 주문 동기화를 시작합니다...")
    # 시작 시 동기화 로직과 동일한 로직을 수행합니다.
    await sync_orders_on_startup()
    print(f"[{datetime.utcnow().isoformat()}] 주기적인 주문 동기화가 완료되었습니다.")

@periodic_sync_task.before_loop
async def before_periodic_sync():
    """백그라운드 작업이 시작되기 전에 봇이 준비될 때까지 기다립니다."""
    await bot.wait_until_ready()


# --- Discord 이벤트 핸들러 ---
@bot.event
async def on_ready():
    print(f'{bot.user.name} 봇이 준비되었습니다.')
    # 데이터베이스 초기화 (테이블이 없으면 생성)
    db.init_db()
    # 봇이 준비되면 초기 주문 동기화 실행
    await sync_orders_on_startup()
    # 초기 동기화 후, 주기적인 백그라운드 동기화 작업을 시작합니다.
    periodic_sync_task.start()


# --- 거래 명령어 (리팩토링됨) ---
@bot.command(name='주문')
@commands.is_owner() # 봇 소유자만 실행 가능하도록 제한
async def place_order(ctx, symbol: str, side: str, quantity: float, price: float = None):
    """선물 주문을 실행합니다. 예:!주문 BTCUSDT BUY 0.01"""
    
    side = side.upper()
    if side not in:
        await ctx.send("잘못된 주문 방향입니다. 'BUY' 또는 'SELL'을 사용해주세요.")
        return

    order_type = 'LIMIT' if price else 'MARKET'
    
    order_params = {
        'symbol': symbol.upper(),
        'side': side,
        'type': order_type,
        'quantity': quantity,
    }
    if order_type == 'LIMIT':
        order_params['price'] = price
        order_params['timeInForce'] = 'GTC'

    # 1. 주문 의도를 데이터베이스에 먼저 기록
    local_order_id, client_order_id = db.create_order_record(order_params)
    await ctx.send(f"주문 기록 생성됨 (로컬 ID: {local_order_id}). 바이낸스에 전송을 시도합니다...")

    try:
        # 2. 실제 주문을 바이낸스에 전송 (고유 ID 포함)
        binance_order = client.futures_create_order(
            newClientOrderId=client_order_id,
            **order_params
        )
        
        # 3. 주문 성공 시, DB 상태를 바이낸스 응답 기준으로 업데이트
        db.update_order_from_binance(local_order_id, binance_order)
        await ctx.send(f"✅ 주문 성공!\n"
                       f"심볼: {binance_order['symbol']}\n"
                       f"바이낸스 ID: {binance_order['orderId']}\n"
                       f"상태: {binance_order['status']}")

    except BinanceAPIException as e:
        # 4. API 오류 발생 시, DB 상태를 'REJECTED'로 업데이트
        db.update_order_status(local_order_id, 'REJECTED')
        await ctx.send(f"❌ 주문 실패 (API 오류): {e}")
    except Exception as e:
        # 5. 기타 알 수 없는 오류 발생 시, DB 상태를 'REJECTED'로 업데이트
        db.update_order_status(local_order_id, 'REJECTED')
        await ctx.send(f"❌ 주문 실패 (알 수 없는 오류): {e}")

# --- 봇 실행 ---
if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN or not BINANCE_API_KEY or not BINANCE_API_SECRET:
        print("오류:.env 파일에 DISCORD_BOT_TOKEN, BINANCE_API_KEY, BINANCE_API_SECRET을 설정해야 합니다.")
    else:
        # datetime 임포트가 필요하므로 추가합니다.
        from datetime import datetime
        bot.run(DISCORD_BOT_TOKEN)
