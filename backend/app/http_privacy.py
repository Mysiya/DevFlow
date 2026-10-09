"""Disable private API response caching without buffering streaming bodies."""
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class PrivateAPIResponses:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path == "/api" or path.startswith("/api/")):
            return await self.app(scope, receive, send)

        async def send_private(message: Message):
            if message["type"] == "http.response.start":
                # Replace rather than append: conflicting upstream cache policy
                # must not make authenticated data eligible for shared caches.
                headers = [(key, value) for key, value in message.get("headers", [])
                           if key.lower() not in (b"cache-control", b"pragma", b"expires")]
                message = {**message, "headers": headers + [
                    (b"cache-control", b"private, no-store, max-age=0"),
                    (b"pragma", b"no-cache"),
                    (b"expires", b"0"),
                ]}
            await send(message)

        await self.app(scope, receive, send_private)
