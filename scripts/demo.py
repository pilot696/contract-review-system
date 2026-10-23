#!/usr/bin/env python
"""无头闭环演示：需要 Mock 审批系统与主服务已启动（推荐先运行 run_all.py）。

用法（仓库根目录）：
    uv run python scripts/demo.py

流程：重置演示数据 → 拉取待办 → 处理 AP001（低风险全闭环 + 幂等回写）
→ AP002 注入故障 blocked → 清障重试（高风险）→ AP003 扫描件 OCR → AP004 缺附件阻塞。
结果写入 demo_report.json；全部断言通过时退出码 0。
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "demo_report.json"
APP_URL = "http://127.0.0.1:8000"
MOCK_URL = "http://127.0.0.1:8100"
_TZ = timezone(timedelta(hours=8))

EXPECTED_AP002 = {"R001", "R002", "R003", "R004", "R005", "R011"}
EXPECTED_AP003 = {"R007", "R008", "R009", "R010"}

steps: list[dict] = []


def step(name: str, ok: bool, detail: str = "") -> bool:
    steps.append({"name": name, "ok": ok, "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    return ok


def wait_task(app: httpx.Client, code: str, timeout: float = 180.0) -> dict:
    last: dict = {}
    deadline = time.time() + timeout
    while time.time() < deadline:
        last = app.get(f"/api/tasks/{code}").json()["data"]["task"]
        if last["task_status"] in {"done", "blocked"}:
            return last
        time.sleep(0.5)
    return last


def review_codes(app: httpx.Client, code: str) -> set[str]:
    hits = app.get(f"/api/tasks/{code}/review").json()["data"]["hits"]
    return {hit["rule_code"] for hit in hits if hit["hit_status"] == "hit"}


def main() -> int:
    report: dict = {
        "generated_at": datetime.now(_TZ).isoformat(timespec="seconds"),
        "app_url": APP_URL,
        "mock_url": MOCK_URL,
        "steps": steps,
    }
    try:
        # 本机服务：显式绕过系统代理，避免被代理转发（代理会返回 502）
        app = httpx.Client(base_url=APP_URL, timeout=180.0, trust_env=False)
        mock = httpx.Client(base_url=MOCK_URL, timeout=30.0, trust_env=False)
    except httpx.HTTPError as exc:
        print(f"client init failed: {exc}")
        return 1

    try:
        health = app.get("/api/health").json()
        if not step("健康检查", health.get("status") == "ok" and health.get("db") == "ok", str(health)):
            return 1

        reset = app.post("/api/admin/reset")
        step("重置演示数据", reset.status_code == 200)

        pulled = app.post("/api/tools/list_pending_contract_approvals", json={"limit": 10}).json()["data"]
        step("拉取待办", pulled["total"] == 4, f"total={pulled['total']}")

        for code in ("AP20261021001", "AP20261021002", "AP20261021003", "AP20261021004"):
            app.post("/api/tools/get_contract_approval", json={"instance_id": code})

        # --- AP001：标准合同，完整闭环（低风险） ---
        app.post("/api/tasks/AP20261021001/process")
        task = wait_task(app, "AP20261021001")
        step(
            "AP001 全流程完成",
            task["task_status"] == "done" and task["write_status"] == "success",
            f"{task['task_status']}/{task['write_status']}",
        )
        result = app.get("/api/tasks/AP20261021001/result").json()["data"]
        step("AP001 低风险", result["overall_risk_level"] == "low", result["overall_risk_level"])
        comments_1 = mock.get("/api/approvals/AP20261021001/comments").json()["total"]
        step("AP001 评论回写 1 条", comments_1 == 1, f"comments={comments_1}")

        replay = app.post(
            "/api/tools/write_approval_comment",
            json={"instance_id": "AP20261021001", "review_id": result["review_id"]},
        ).json()["data"]
        step(
            "重复回写幂等",
            replay["idempotent_replay"] is True and replay["write_status"] == "success",
            str(replay["idempotent_replay"]),
        )
        step(
            "幂等后评论仍 1 条",
            mock.get("/api/approvals/AP20261021001/comments").json()["total"] == 1,
        )

        # --- AP002：注入缺附件故障 → blocked → 清障重试 → 高风险 ---
        app.post(
            "/api/admin/faults",
            json={"mode": "missing_attachment", "approval_code": "AP20261021002"},
        )
        app.post("/api/tasks/AP20261021002/process")
        blocked = wait_task(app, "AP20261021002")
        step(
            "AP002 缺附件进入 blocked",
            blocked["task_status"] == "blocked"
            and blocked["failed_stage"] == "download"
            and blocked["blocked_reason"] == "ATTACHMENT_MISSING",
            f"{blocked['task_status']}/{blocked['failed_stage']}/{blocked['blocked_reason']}",
        )

        app.delete("/api/admin/faults")
        app.post("/api/tasks/AP20261021002/retry")
        retried = wait_task(app, "AP20261021002")
        step(
            "AP002 重试成功",
            retried["task_status"] == "done"
            and retried["write_status"] == "success"
            and retried["retry_count"] == 1,
            f"{retried['task_status']}/{retried['write_status']}/retry={retried['retry_count']}",
        )
        codes_2 = review_codes(app, "AP20261021002")
        step("AP002 命中 6 条且符合预期", codes_2 == EXPECTED_AP002, ",".join(sorted(codes_2)))
        step(
            "AP002 回写 1 条",
            mock.get("/api/approvals/AP20261021002/comments").json()["total"] == 1,
        )

        # --- AP003：扫描件 OCR 路径 ---
        app.post("/api/tasks/AP20261021003/process")
        task_3 = wait_task(app, "AP20261021003")
        step(
            "AP003 扫描件闭环完成",
            task_3["task_status"] == "done" and task_3["write_status"] == "success",
            f"{task_3['task_status']}/{task_3['write_status']}",
        )
        parse_items = app.get("/api/tasks/AP20261021003/parse").json()["data"]["items"]
        step("AP003 使用 OCR", bool(parse_items) and parse_items[0]["ocr_used"] is True)
        codes_3 = review_codes(app, "AP20261021003")
        step("AP003 命中 4 条且符合预期", codes_3 == EXPECTED_AP003, ",".join(sorted(codes_3)))

        # --- AP004：天然缺附件 → blocked ---
        app.post("/api/tasks/AP20261021004/process")
        task_4 = wait_task(app, "AP20261021004")
        step(
            "AP004 缺附件 blocked",
            task_4["task_status"] == "blocked"
            and task_4["blocked_reason"] == "ATTACHMENT_MISSING",
            str(task_4["blocked_reason"]),
        )

        stats = app.get("/api/stats").json()["data"]
        step("统计接口可用", stats["rules_active"] == 11, json.dumps(stats, ensure_ascii=False))

        report["summary"] = {
            "passed": sum(1 for item in steps if item["ok"]),
            "failed": sum(1 for item in steps if not item["ok"]),
            "tasks_total": app.get("/api/tasks").json()["data"]["total"],
            "stats": stats,
        }
    finally:
        app.close()
        mock.close()

    report["ok"] = report.get("summary", {}).get("failed", 1) == 0
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"[demo] report written: {REPORT_PATH}")
    print(
        f"[demo] passed={report['summary']['passed']} failed={report['summary']['failed']} "
        f"-> {'OK' if report['ok'] else 'FAIL'}"
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
