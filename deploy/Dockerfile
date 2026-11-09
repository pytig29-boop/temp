# btc-quant 실시간 운용 이미지 (collector / paper / nightly research 공용)
# 빌드: 저장소 루트에서  docker compose -f deploy/docker-compose.yml build
# 주의: 빌드 컨텍스트가 저장소 루트이므로 data/, research/ 등 대용량 디렉터리는
#       .dockerignore(루트) 또는 deploy/Dockerfile.dockerignore 로 제외할 것 (docs/OPERATIONS.md).
FROM ghcr.io/astral-sh/uv:0.9.7-python3.13-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul

WORKDIR /app

# 1) 의존성만 먼저 (레이어 캐시)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# 2) 프로젝트 소스
COPY src ./src
COPY README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# 3) 비루트 사용자, 데이터/런타임 디렉터리는 볼륨으로 마운트
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && mkdir -p /app/data /app/runtime /app/research /app/logs \
 && chown -R app:app /app/data /app/runtime /app/research /app/logs

ENV PATH=/opt/venv/bin:$PATH
USER app

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD ["python", "-m", "btc_quant.live", "healthcheck", "--heartbeat", "/app/runtime/collector.heartbeat"]

CMD ["python", "-m", "btc_quant.live", "collect", "--exchanges", "coinone,upbit"]
