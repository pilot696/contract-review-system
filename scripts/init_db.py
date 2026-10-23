#!/usr/bin/env python
"""初始化数据库：创建 8 张表（幂等）。

用法（在仓库根目录）：
    uv run python scripts/init_db.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.models  # noqa: E402,F401  确保所有模型注册到 Base.metadata
from sqlalchemy import inspect, text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import Base, engine  # noqa: E402


def main() -> int:
    settings = get_settings()
    target = settings.database_url.split("@")[-1]
    print(f"[init_db] target database: {target}")

    Base.metadata.create_all(bind=engine)

    # 轻量列迁移：规则引擎需要解析全文与文本块（旧表不会被 create_all 加列）
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE contract_parses ADD COLUMN IF NOT EXISTS full_text TEXT"))
        conn.execute(
            text(
                "ALTER TABLE contract_parses "
                "ADD COLUMN IF NOT EXISTS text_blocks_json JSONB DEFAULT '[]'::jsonb"
            )
        )

    inspector = inspect(engine)
    tables = sorted(inspector.get_table_names(schema="public"))
    print(f"[init_db] tables created/found ({len(tables)}): {', '.join(tables)}")

    with engine.connect() as conn:
        server_version = conn.execute(text("SHOW server_version")).scalar_one()
    print(f"[init_db] connected: PostgreSQL {server_version}")
    print("[init_db] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
