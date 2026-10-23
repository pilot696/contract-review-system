#!/usr/bin/env python
"""重置演示数据：清空业务表、附件文件与 Mock 状态；保留规则表。

用法（仓库根目录）：
    uv run python scripts/reset_demo.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import session_scope  # noqa: E402
from mock_approval.store import STORE  # noqa: E402

_TRUNCATE = (
    "TRUNCATE task_logs, comment_logs, review_results, rule_hits, contract_parses, "
    "approval_attachments, approval_tasks RESTART IDENTITY CASCADE"
)


def _http_reset(settings) -> bool:
    try:
        response = httpx.post(
            f"{settings.approval_system_base_url.rstrip('/')}/admin/reset",
            timeout=2.0,
            trust_env=False,
        )
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def main() -> int:
    settings = get_settings()
    mock_reset = _http_reset(settings)

    with session_scope() as db:
        db.execute(text(_TRUNCATE))
    print("[reset_demo] business tables truncated")

    STORE.reset()
    print("[reset_demo] in-process mock store reset", "(remote mock also reset)" if mock_reset else "")

    attachment_root = Path(settings.attachment_root)
    removed = 0
    if attachment_root.exists():
        for child in attachment_root.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
                removed += 1
            else:
                child.unlink()
                removed += 1
    print(f"[reset_demo] attachment files removed: {removed}")

    with session_scope() as db:
        rules = db.execute(text("SELECT count(*) FROM review_rules")).scalar_one()
    print(f"[reset_demo] rules kept: {rules}")
    print("[reset_demo] OK" + ("" if mock_reset else "（Mock 服务未运行，评论/故障为进程内新状态）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
