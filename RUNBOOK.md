# FTM2 운영 런북 (요약)

> 이 문서는 업로드하신 압축파일(`ftm2-dev-main.zip`)에서 확인한 코드 기준으로 **실행/운영** 방법을 한글로 정리한 것입니다.

## 0) 코드 점검 요약
- 포함 폴더: `ftm2/`(app, core, data, trade, forecast, metrics, monitor, ops, replay, strategy, backtest 등), `Dockerfile`, `docker-compose.yml`, `.env.sample`, `token.env.sample`, `patch.txt`, `docs/` ✔️
- 핵심 모듈 존재: 
  - 실행 엔진: `ftm2/app.py`
  - 듀얼 모드: `DATA_MODE`/`TRADE_MODE` (라이브 차트 + 테스트넷 주문 가능)
  - HTTP 모니터링: `ftm2/ops/httpd.py` (/healthz, /readyz, /metrics, /kpi)
  - 실행 품질/원장: `ftm2/metrics/exec_quality.py`, `ftm2/metrics/order_ledger.py`
  - KPI: `ftm2/monitor/kpi.py`
  - 리플레이/백테스트: `ftm2/replay/engine.py`, `ftm2/backtest/runner.py`
- 구문 검사: 전체 파이썬 소스 컴파일 OK (문법 에러 없음).
- 외부 의존: **전부 필수는 아님**. 기본 실행은 표준 라이브러리로 동작하고, 실거래소/디스코드 사용 시에만 선택 라이브러리가 필요합니다.

## 1) 꼭 필요한 파일 두 개 (.env / token.env)
- `.env.sample`을 복사해서 `.env`로 만듭니다.
- `token.env.sample`을 복사해서 `token.env`로 만듭니다. (이 파일은 **비공개**로 보관)

### 최소 설정 예시(테스트넷 주문, 실선물 차트 분석)
**.env**
```
DATA_MODE=live
TRADE_MODE=testnet
EXEC_ACTIVE=1

OPS_HTTP_ENABLED=true
OPS_HTTP_PORT=8080
LOG_LEVEL=INFO
```

**token.env**
```
BINANCE_TESTNET_API_KEY=<당신의_테스트넷_API_KEY>
BINANCE_TESTNET_API_SECRET=<당신의_테스트넷_API_SECRET>
# (선택) 디스코드 봇을 쓰는 경우만 필요
# DISCORD_BOT_TOKEN=<옵션>
```

> **실계좌 전환**시에는 `TRADE_MODE=live`로 바꾸고, `token.env`에 라이브 키 2개를 채워주세요.
>
> **권장 운영 원칙**: “분석은 라이브 차트 / 주문은 테스트넷 → 준비 끝나면 TRADE_MODE만 live로 교체”.

## 2) 도커 없이 실행(로컬)
```bash
# (선택) 필요한 서드파티 라이브러리
python -m pip install httpx websocket-client discord.py

# .env / token.env 준비 후
python -m ftm2.ops.doctor           # 사전 점검 (네트워크/DB/모드/포트)
python -m ftm2.app                  # 메인 앱 실행
```

정상이라면:
- 콘솔 로그에 초기화 로그가 나오고
- `http://localhost:8080/healthz` → `ok`
- `http://localhost:8080/readyz` → `ready` (라이브 차트 수신 중일 때)
- `http://localhost:8080/kpi` → KPI JSON 이 보입니다.

## 3) 도커로 실행
```bash
# 1) .env / token.env 두 파일을 리포 루트에 둔다.
# 2) 이미지 빌드
docker compose --profile testnet build
# 3) 실행 (실선물 차트 + 테스트넷 주문)
docker compose --profile testnet up -d

# 상태 확인
curl -s http://localhost:8080/healthz
curl -s http://localhost:8080/readyz
docker compose --profile testnet logs -f
```

- **드라이런(주문 미발행)**: `--profile dry`
- **실계좌**: `--profile live` (키/리스크 확인 후 사용)

## 4) HTTP 모니터링 엔드포인트
- `GET /healthz` → 200 "ok" (프로세스)
- `GET /readyz` → 200 "ready" (시세 최신성 판단)
- `GET /metrics` → Prometheus 텍스트
- `GET /kpi` → KPI 스냅샷(JSON)

## 5) 주요 런타임 구성요소
- **StateBus(스냅샷)**: 모든 모듈이 상태를 이 버스로 읽고/씁니다.
- **Risk/Guard**: 포지션 사이징/레버 한도/데일리컷 보호.
- **ExecQuality/OrderLedger**: 슬리피지/취소/체결률/TTF(ms) 리포트.
- **KPI**: 전략·리스크·실행 지표를 텍스트 패널로 생성(디스코드 옵션).
- **Dual Mode**: `DATA_MODE=live` + `TRADE_MODE=testnet|live` 구성.

## 6) 문제 해결(FAQ)
- `/readyz`가 `stale`: 라이브 시세가 들어오지 않는 상태. 방화벽/네트워크 또는 거래소 WS 연결 확인.
- 주문이 안나가요: `EXEC_ACTIVE=1`, `TRADE_MODE=testnet|live`, 키 값 유효 확인.
- 디스코드 패널이 안나와요: `DISCORD_BOT_TOKEN` 필요. 그렇지 않으면 파일 큐만 기록됩니다.
- 로그 파일 경로: 기본 `/var/log/ftm2/app.log` (도커는 `./logs`로 마운트).

## 7) 백테스트/리플레이(선택)
- **리플레이**: `.env`에 `REPLAY_ENABLED=1`, `REPLAY_SRC=./data/replay.ndjson`
- **백테스트**: CSV 1분봉 파일 준비 후 `python -m ftm2.backtest` 실행 → `./reports/`에 결과 CSV 3종 출력.

---

✅ 위 순서대로 진행하면 “라이브 차트 분석 + 테스트넷 주문”까지 바로 확인할 수 있습니다.
