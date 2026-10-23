"""本机回环代理绕过测试：系统开了 HTTP 代理时，127.0.0.1/localhost 不能被代理转发。"""

from __future__ import annotations

from app.services.approval_client import _is_loopback

LOCAL_URLS = [
    "http://127.0.0.1:8100",
    "http://127.0.0.1",
    "http://localhost:8100",
    "http://localhost",
    "http://[::1]:8100",
]

REMOTE_URLS = [
    "http://192.168.200.188:8100",
    "https://approval.example.com",
    "http://10.0.0.5:8000",
]


def test_loopback_detection() -> None:
    assert all(_is_loopback(url) for url in LOCAL_URLS)
    assert not any(_is_loopback(url) for url in REMOTE_URLS)
