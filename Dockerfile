# 可选：应用镜像（数据库仍使用外部 PostgreSQL）。
# 当前验收路径为本机 uvicorn 运行，Dockerfile 仅作为部署备选，未纳入自动化验收。
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 使用官方 uv 镜像层安装依赖（利用 lock 文件）
COPY --from=ghcr.io/astral-sh/uv:0.4 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra parsing --extra mcp

COPY app ./app
COPY mock_approval ./mock_approval
COPY scripts ./scripts

EXPOSE 8000
CMD ["uv", "run", "--no-dev", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
