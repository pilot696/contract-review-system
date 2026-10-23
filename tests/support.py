"""测试支撑：把 ASGI 应用桥接成 httpx 同步 transport（不占端口、无需起服务）。"""

from __future__ import annotations

import asyncio

import httpx


class SyncASGITransport(httpx.BaseTransport):
    """在同步 httpx.Client 中驱动 ASGI 应用（每个请求一个事件循环）。"""

    def __init__(self, app) -> None:
        self._app = app

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        content = request.read()
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": request.method,
            "scheme": request.url.scheme,
            "path": request.url.path,
            "raw_path": request.url.raw_path.split(b"?")[0],
            "query_string": request.url.query,
            "headers": [(key.lower(), value) for key, value in request.headers.raw],
            "client": ("127.0.0.1", 12345),
            "server": ("mock-approval", 80),
            "root_path": "",
        }

        async def _call():
            start: dict = {}
            body = bytearray()

            async def receive():
                return {"type": "http.request", "body": content, "more_body": False}

            async def send(message: dict) -> None:
                if message["type"] == "http.response.start":
                    start.update(message)
                elif message["type"] == "http.response.body":
                    body.extend(message.get("body", b""))

            await self._app(scope, receive, send)
            return start, bytes(body)

        start, body = asyncio.run(_call())
        headers = [(key.decode("latin-1"), value.decode("latin-1"))
                   for key, value in start.get("headers", [])]
        return httpx.Response(
            status_code=start["status"],
            headers=headers,
            content=body,
            request=request,
        )
