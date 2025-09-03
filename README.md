# FTM2 — Binance USDⓈ-M Futures (Testnet↔Live Toggle) Trading System

> **소스 오브 트루스**: “Binance USDⓈ-M 선물(테스트넷↔실계좌 토글) **실매매 프로그램 — 상세 설계서(v1)**”을 상시 참조.
> “상세 마일스톤(티켓화)” 문서의 티켓 규칙을 그대로 따릅니다.

## 목적
- 테스트넷 우선 설계 → 토큰 교체만으로 라이브 전환
- BTCUSDT/ETHUSDT 2심볼, TF 다중(1m/5m/15m/1h/4h)
- Discord 컨트롤/대시보드/알림 일원화
- 기능추가 로그는 **patch.txt**에 기록(버그픽스 제외)

---

## 브랜치/PR/커밋 컨벤션 (M0.1)
- **브랜치**: `main`(안정) / `ftm2-dev`(개발) / `feat/*`(티켓별)
- **PR**: 1티켓=1PR, 제목 예시: `[M1.2][BINANCE_CLIENT] testnet/live 토글`
- **커밋 메시지**: `type(scope): summary` (type: feat/fix/docs/chore/test/refactor)

## 앵커 주석 규칙
- 형식: `# [ANCHOR:BINANCE_CLIENT]` 처럼 대문자 스네이크
- 주요 섹션마다 1개 이상 표기하여 코드 탐색/패치 지점을 고정

## 디렉토리 구조
```
ftm2/
  core/ exchange/ data/ signal/ trade/ discord_bot/ tests/ runtime/
docs/
patch.txt
.env.sample
token.env.sample
```
빈 디렉토리는 `.gitkeep`로 유지.

---

## ENV 체인 & 보안 규칙 (M0.2)
- 키는 `token.env`로 분리해 보관(커밋 금지). `.gitignore`에 *.env 포함.
- 로드 순서: `os.environ` > `token.env` > `.env`
- 샘플 키는 `.env.sample` & `token.env.sample` 참고.

### .env.sample — 초기 키
```
# [ENV:CORE]
MODE=testnet
SYMBOLS=BTCUSDT,ETHUSDT
TF_EXEC=1m
TF_SIGNAL=5m,15m,1h,4h

# [ENV:EXCHANGE]
BINANCE_TESTNET_API_KEY=
BINANCE_TESTNET_API_SECRET=
BINANCE_LIVE_API_KEY=
BINANCE_LIVE_API_SECRET=

# [ENV:RISK]
RISK_TARGET_PCT=0.30
DAILY_MAX_LOSS_PCT=3.0
CORR_CAP_PER_SIDE=0.65

# [ENV:DISCORD]
DISCORD_BOT_TOKEN=
CHAN_ANALYSIS_ID=
CHAN_DASHBOARD_ID=
CHAN_PANEL_ID=
CHAN_ALERTS_ID=

# [ENV:MISC]
DB_PATH=./runtime/trader.db
CONFIG_PATH=./runtime/config.json
PATCH_LOG=./patch.txt
```

### token.env.sample — 민감 키
```
BINANCE_TESTNET_API_KEY=REDACTED
BINANCE_TESTNET_API_SECRET=REDACTED
BINANCE_LIVE_API_KEY=REDACTED
BINANCE_LIVE_API_SECRET=REDACTED
DISCORD_BOT_TOKEN=REDACTED
```

---

## patch.txt 운영 (M0.3)
- **오직 기능 추가(feat)만 기록** — 오류 수정/리팩터는 제외.
- 포맷:
```
YYYY-MM-DD vX.Y.Z
- feat(scope): 요약
- feat(scope): 요약
```
- DB `patches` 테이블과 동기, Discord `/patch log`로 노출.

예시(참고용, 실제 릴리스 시 이 블록은 삭제 가능):
```
2025-09-03 v0.1.0
- feat(state): 전역 스냅샷 버스 규격 확정
- feat(discord): /mode /auto /close 명령 스펙 고정
- feat(risk): ATR-unit 파라미터 세트 확정(R%, day-cut, corr-cap)
```

---

## Discord 채널 & 명령 (M0.4)
- 채널: `#분석`, `#대시보드`, `#컨트롤패널`, `#알림`
- 권한: `/mode live`는 Admin 2단 확인(“ARM_LIVE” 확인어)

| 명령 | 인자 | 설명 | 권한 |
|---|---|---|---|
| `/mode` | `paper|testnet|live` | 실행 모드 전환 | Admin |
| `/auto` | `on|off` | 자동 매매 토글 | Operator |
| `/close` | `all|BTC|ETH` | 포지션 청산 | Operator |
| `/reverse` | `BTC|ETH` | 역진입(실험용) | Admin |
| `/flat` | `BTC|ETH` | 해당 심볼 제로화 | Operator |

UI 위젯(초기): 슬라이더(`target_R%`, `horizon_k`, `max_adds`, `corr_cap`, `risk_stop_day%`) / 토글(`SCOUT_ONLY`, `STRONG_SIGNAL_BYPASS`, `TRAIL_ENABLE`).

---

## 티켓 템플릿
아래 템플릿을 **이슈/PR 본문**에 사용합니다.
```
[제목: [Mx.y] <티켓명>
앵커/파일: [ANCHOR:XXXX] in <path/to/file.py>
목표/스코프: (한 줄 요약)
입력/출력 계약: (함수 시그니처/딕트 스키마/단위/예시)
ENV 토글: (키 이름과 기본값)
로그/에러 코드: (기대 로그 샘플과 에러 맵)
DoD: (수용 기준—테스트/로그 표본/성능 목표)
patch.txt: (feat: ... 로 한 줄 요약—기능 추가에 한함)]
```

---

## M0 체크리스트
- [ ] 브랜치/PR/커밋 규칙 README 반영
- [ ] 디렉토리 트리 생성(.gitkeep 포함)
- [ ] `.env.sample`/`token.env.sample` 배치
- [ ] `.gitignore` 반영
- [ ] `patch.txt` 생성(기능 추가 로그 전용)
- [ ] Discord 채널/권한/명령 예약
- [ ] “설계서(v1)” 및 “상세 마일스톤(티켓화)” 문서를 /docs 에 두고 레퍼런스 고정
