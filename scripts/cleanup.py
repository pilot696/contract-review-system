#!/usr/bin/env python
"""清理脚本：过期日志 + 孤儿附件文件。

用法（仓库根目录）：
    uv run python scripts/cleanup.py                    # 清理 90 天前日志 + 孤儿附件
    uv run python scripts/cleanup.py --days 30
    uv run python scripts/cleanup.py --dry-run          # 只统计不删除
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.models import ApprovalAttachment, CommentLog, TaskLog  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="cleanup logs and orphan attachments")
    parser.add_argument("--days", type=int, default=90, help="日志保留天数（默认 90）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不删除")
    args = parser.parse_args()

    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    with session_scope() as db:
        old_logs = db.execute(
            delete(TaskLog).where(TaskLog.created_at < cutoff)
        ).rowcount
        old_comments = db.execute(
            delete(CommentLog).where(CommentLog.created_at < cutoff)
        ).rowcount
        known_paths = {
            Path(path).resolve()
            for path in db.scalars(select(ApprovalAttachment.file_path)).all()
            if path
        }

    attachment_root = Path(settings.attachment_root)
    orphans: list[Path] = []
    if attachment_root.exists():
        for path in attachment_root.rglob("*"):
            if path.is_file() and path.resolve() not in known_paths:
                orphans.append(path)

    if args.dry_run:
        print(f"[cleanup] dry-run: task_logs={old_logs}, comment_logs={old_comments}, orphans={len(orphans)}")
        return 0

    removed = 0
    for path in orphans:
        try:
            path.unlink()
            removed += 1
        except OSError as exc:  # pragma: no cover - 文件占用等
            print(f"[cleanup] skip {path}: {exc}")

    print(f"[cleanup] deleted task_logs={old_logs}, comment_logs={old_comments}, orphan files={removed}")
    print("[cleanup] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
