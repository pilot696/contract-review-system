#!/usr/bin/env python
"""一条命令拉起 Mock 审批系统（:8100）与主服务（:8000）。

用法（仓库根目录）：
    uv run python scripts/run_all.py            # 默认确定性模式（LLM_ENABLED=false，demo/验收可复现）
    uv run python scripts/run_all.py --llm      # 启用 .env 中的 LLM 补抽
    uv run python scripts/run_all.py --reload   # uvicorn 自动重载（开发）
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402


def _spawn(
    module: str, host: str, port: int, reload: bool, env: dict[str, str]
) -> subprocess.Popen:
    args = [sys.executable, "-m", "uvicorn", module, "--host", host, "--port", str(port)]
    if reload:
        args.append("--reload")
    print(f"[run_all] start: {' '.join(args)}")
    return subprocess.Popen(args, cwd=str(ROOT), env=env)


def _wait(url: str, name: str, timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            # 本机回环必须绕过系统代理
            response = httpx.get(url, timeout=2.0, trust_env=False)
            if response.status_code < 500:
                print(f"[run_all] ready: {name} -> {url}")
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    print(f"[run_all] TIMEOUT waiting for {name}: {url}")
    return False


def main() -> int:
    settings = get_settings()
    mock_host, mock_port = settings.mock_host, settings.mock_port
    app_host, app_port = settings.app_host, settings.app_port
    reload = "--reload" in sys.argv
    env = os.environ.copy()
    # 本机回环绕过系统代理：Windows 系统代理配置会让 127.0.0.1 请求被转发并返回 502
    for proxy_key in ("NO_PROXY", "no_proxy"):
        env[proxy_key] = "127.0.0.1,localhost,::1"
    if "--llm" not in sys.argv:
        # 演示/验收默认关闭 LLM：字段补抽结果不进入规则命中，保证结果可复现
        env["LLM_ENABLED"] = "false"
        print("[run_all] LLM: off（确定性模式；需要补抽时加 --llm）")
    else:
        print("[run_all] LLM: on（使用 .env 配置）")

    mock = _spawn("mock_approval.main:app", mock_host, mock_port, reload, env)
    app = _spawn("app.main:app", app_host, app_port, reload, env)
    processes = [mock, app]
    try:
        ok = _wait(f"http://{mock_host}:{mock_port}/healthz", "Mock 审批系统")
        ok = _wait(f"http://{app_host}:{app_port}/api/health", "主服务") and ok
        if not ok:
            return 1
        print()
        print(f"[run_all] Web 控制台 : http://{app_host}:{app_port}/console")
        print(f"[run_all] API 文档   : http://{app_host}:{app_port}/docs")
        print(f"[run_all] Mock 服务  : http://{mock_host}:{mock_port}/healthz")
        print("[run_all] Ctrl+C 停止两个服务")
        while True:
            for process in processes:
                code = process.poll()
                if code is not None:
                    print(f"[run_all] a service exited unexpectedly (code={code})")
                    return 1
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[run_all] stopping...")
        return 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        deadline = time.time() + 10
        for process in processes:
            try:
                process.wait(timeout=max(1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                process.kill()
        print("[run_all] stopped")


if __name__ == "__main__":
    raise SystemExit(main())
