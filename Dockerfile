# ---------- builder ----------
FROM python:3.11-slim AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# (선택) 요구사항 먼저 복사해 캐시 히트
COPY requirements.txt* /app/
RUN if [ -f requirements.txt ]; then pip wheel --wheel-dir /wheels -r requirements.txt; fi
COPY . /app
# 패키징이 없으면 editable 설치용 휠 생성 없이 소스 그대로 사용

# ---------- runtime ----------
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 \
    TZ=Etc/UTC
# 보안상 비루트
RUN useradd -m -u 10001 appuser
WORKDIR /app
# 런타임 의존 설치
COPY --from=builder /wheels /wheels
RUN if [ -d /wheels ]; then pip install --no-cache-dir /wheels/*; fi
# 소스 배포
COPY --chown=appuser:appuser . /app
# (requirements.txt가 없으면) 에디터블 설치 생략, 패키지 구조 아니어도 모듈로 실행
# 런 커맨드/엔트리포인트
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
 && mkdir -p /var/log/ftm2 /data \
 && chown -R appuser:appuser /var/log/ftm2 /data
USER appuser
EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
