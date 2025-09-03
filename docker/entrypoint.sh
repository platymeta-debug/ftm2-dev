#!/usr/bin/env bash
set -euo pipefail

# 한글 로그를 위해 로케일/TZ 기본 셋업
export TZ="${TZ:-Etc/UTC}"

# 기본 모드 보정 (듀얼 모드: DATA_MODE/TRADE_MODE)
export DATA_MODE="${DATA_MODE:-live}"
export TRADE_MODE="${TRADE_MODE:-dry}"

# 실행 로그/레벨 기본
export LOG_LEVEL="${LOG_LEVEL:-INFO}"
export LOG_DIR="${LOG_DIR:-/var/log/ftm2}"
export LOG_FILE="${LOG_FILE:-app.log}"
export LOG_MAX_BYTES="${LOG_MAX_BYTES:-10485760}"   # 10MB
export LOG_BACKUPS="${LOG_BACKUPS:-5}"

# ops http 포트
export OPS_HTTP_PORT="${OPS_HTTP_PORT:-8080}"

# 안내
echo "[ENTRY] START FTM2 (DATA_MODE=${DATA_MODE} / TRADE_MODE=${TRADE_MODE} / LOG=${LOG_DIR}/${LOG_FILE})"

# 마이그레이션/스키마 보증이 필요하면 여기에 배치(현재는 스킵)
# python -m ftm2.tools.migrate || true

# 앱 실행
exec python -m ftm2.app "$@"
