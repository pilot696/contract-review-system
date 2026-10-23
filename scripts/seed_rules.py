#!/usr/bin/env python
"""写入/更新 11 条种子审查规则（幂等 upsert）。

用法（仓库根目录）：
    uv run python scripts/seed_rules.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.models import ReviewRule  # noqa: E402
from app.services.rules.seed import RULES_SEED, upsert_seed_rules  # noqa: E402


def main() -> int:
    with session_scope() as db:
        seeded = upsert_seed_rules(db)

    with session_scope() as db:
        total = db.scalar(select(func.count()).select_from(ReviewRule)) or 0
        active = db.scalar(
            select(func.count()).select_from(ReviewRule).where(ReviewRule.rule_status == "active")
        ) or 0
        codes = list(db.scalars(select(ReviewRule.rule_code).order_by(ReviewRule.rule_code)).all())

    print(f"[seed_rules] seeded {seeded} rules (total={total}, active={active})")
    print(f"[seed_rules] codes: {', '.join(codes)}")
    print("[seed_rules] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
