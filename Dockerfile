# ---- 阶段1：前端占位（Task 13 后替换为 npm ci + npm run build）----
FROM node:20-slim AS fe
WORKDIR /fe
COPY frontend/ ./
RUN mkdir -p dist && echo "frontend placeholder" > dist/index.html

# ---- 阶段2：Python 运行时（含 dev 组，容器内可跑 pytest）----
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Shanghai PYTHONUNBUFFERED=1 UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen
COPY backend/ backend/
COPY --from=fe /fe/dist/ frontend/dist/
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
