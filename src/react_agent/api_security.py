"""Local single-worker API ingress limits; not a distributed rate limiter."""

import asyncio
import hashlib
import time
from collections import OrderedDict, deque

from starlette.responses import JSONResponse

MAX_BODY_BYTES = 16384
MAX_SESSIONS_PER_CUSTOMER = 25
MAX_TURNS_PER_SESSION = 50


class RateLimiter:
    def __init__(self, *, clock=time.monotonic, max_keys=1024):
        self.clock = clock
        self.max_keys = max_keys
        self.buckets = OrderedDict()

    def allow(self, key, limit, window=60):
        now = self.clock()
        bucket = self.buckets.setdefault(key, deque())
        self.buckets.move_to_end(key)
        while bucket and bucket[0] <= now - window:
            bucket.popleft()
        while len(self.buckets) > self.max_keys:
            self.buckets.popitem(last=False)
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


class IngressLimits:
    """Bound bytes BEFORE FastAPI JSON parsing, including chunked requests."""

    def __init__(self, app, body_timeout=10):
        self.app = app
        self.limiter = RateLimiter()
        self.body_timeout = body_timeout

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def secured_send(message):
            if message["type"] == "http.response.start":
                message = {
                    **message,
                    "headers": list(message.get("headers", []))
                    + [
                        (b"x-content-type-options", b"nosniff"),
                        (b"cache-control", b"no-store"),
                        (b"x-frame-options", b"DENY"),
                    ],
                }
            await send(message)

        async def reject(status, code):
            response = JSONResponse({"detail": {"code": code}}, status_code=status)
            await response(scope, receive, secured_send)

        if not scope["path"].startswith("/v1/"):
            return await self.app(scope, receive, secured_send)
        # Ignore forwarded headers: the launcher does not trust proxy headers.
        peer = (scope.get("client") or ("unknown",))[0]
        key = hashlib.sha256(str(peer).encode()).digest()
        if not self.limiter.allow(key, 60):
            return await reject(429, "ingress_rate_limited")
        headers = dict(scope.get("headers", []))
        if (
            sum(len(key) + len(value) for key, value in scope.get("headers", []))
            > 16384
        ):
            return await reject(431, "headers_too_large")
        try:
            length = int(headers.get(b"content-length", b"0"))
            if length < 0:
                raise ValueError
        except ValueError:
            return await reject(400, "invalid_content_length")
        if length > MAX_BODY_BYTES:
            return await reject(413, "body_too_large")
        body, size = [], 0
        try:
            async with asyncio.timeout(self.body_timeout):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    part = message.get("body", b"")
                    size += len(part)
                    if size > MAX_BODY_BYTES:
                        return await reject(413, "body_too_large")
                    body.append(part)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            return await reject(408, "body_timeout")
        replayed = False

        async def bounded_receive():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {
                    "type": "http.request",
                    "body": b"".join(body),
                    "more_body": False,
                }
            return await receive()

        return await self.app(scope, bounded_receive, secured_send)
