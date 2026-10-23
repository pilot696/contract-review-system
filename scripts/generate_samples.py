#!/usr/bin/env python
"""生成 M2 真实示例合同并刷新 Mock 审批系统的附件哈希。

用法（仓库根目录）：
    uv run python scripts/generate_samples.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from mock_approval.samples import FILES_DIR, generate_all  # noqa: E402
from mock_approval.store import STORE  # noqa: E402


def main() -> int:
    paths = generate_all(force=True)
    print(f"[generate_samples] output dir: {FILES_DIR}")
    for path in paths:
        print(f"  - {path.name}: {path.stat().st_size} bytes")

    # 刷新本进程内 Mock 数据（若 Mock 服务单独运行，则调用其 admin 接口）
    STORE.reset()
    print("[generate_samples] in-process mock store reloaded")

    try:
        response = httpx.post(
            "http://127.0.0.1:8100/admin/reload", timeout=2.0, trust_env=False
        )
        if response.status_code == 200:
            print("[generate_samples] running mock server reloaded via /admin/reload")
    except httpx.HTTPError:
        print("[generate_samples] mock server not running (skip remote reload)")

    print("[generate_samples] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
