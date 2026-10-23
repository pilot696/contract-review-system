# 可选：应用镜像（数据库使用外部 PostgreSQL，连接串通过环境变量注入）。
# 说明：本机 Docker 未启用，本文件按静态审查修正（基础镜像/依赖安装/启动命令/缓存），
#       镜像构建尚未纳入自动化验收；真正部署前请实际构建并跑一次 demo。
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# 使用官方 uv 镜像层（版本与本机 uv 0.12.0 对齐）
COPY --from=ghcr.io/astral-sh/uv:0.12.0 /uv /usr/local/bin/uv

# 先装依赖以利用镜像层缓存；extras 覆盖解析与 MCP
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra parsing --extra mcp

COPY app ./app
COPY mock_approval ./mock_approval
COPY scripts ./scripts
COPY sql ./sql

EXPOSE 8000
# --no-sync：直接使用镜像内已同步的 .venv，避免运行时重新解析依赖
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
