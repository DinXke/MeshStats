"""Request body size limit, enforced while reading rather than on a header.

The API used to trust ``Content-Length``. A chunked request does not send one,
so the check read ``0`` and every oversized body sailed through -- and admin
form posts were never checked at all. Counting bytes as they arrive is the only
version of this limit that a client cannot opt out of.

Written as raw ASGI rather than a Starlette ``BaseHTTPMiddleware``: the point is
to wrap ``receive`` and stop the stream mid-body, which the higher-level
middleware API does not expose.
"""
from . import config


class _BodyTooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    def __init__(self, app, max_bytes: int | None = None):
        self.app = app
        self.max_bytes = config.MAX_BODY_BYTES if max_bytes is None else max_bytes

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    # Honest oversized request: refuse before reading a byte.
                    await _reject(send)
                    return
            except ValueError:
                await _reject(send)
                return

        received = 0
        too_big = False
        started = False
        answered = False

        async def limited_receive():
            nonlocal received, too_big
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    too_big = True
                    # Raising stops the read immediately instead of letting the
                    # endpoint sit there waiting for a body it will never get.
                    raise _BodyTooLarge()
            return message

        async def guarded_send(message):
            nonlocal started, answered
            if answered:
                return  # already replied 413; ignore whatever else the app says
            if message.get("type") == "http.response.start":
                if too_big:
                    # FastAPI catches everything the form and JSON parsers raise
                    # and turns it into its own 400, so the exception above does
                    # not always reach us. Whatever the app decided to answer,
                    # the body was over the limit and 413 is the honest reply.
                    answered = True
                    await _reject(send)
                    return
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLarge:
            if started or answered:
                # The endpoint already answered without reading everything; the
                # client is just still talking. Nothing left to say.
                return
            await _reject(send)


async def _reject(send):
    body = b"Payload te groot"
    await send({
        "type": "http.response.start",
        "status": 413,
        "headers": [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body})
