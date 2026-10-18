import os

from starlette.responses import JSONResponse

MAX_BODY_BYTES = 256 * 1024


def allowed_origins():
    origins = {
        value.strip().rstrip("/")
        for value in os.getenv("VANGUARD_ALLOWED_ORIGINS", "").split(",")
        if value.strip()
    }
    public = os.getenv("VANGUARD_PUBLIC_URL")
    if public:
        from urllib.parse import urlsplit

        url = urlsplit(public)
        origins.add(f"{url.scheme}://{url.netloc}")
    if os.getenv("VANGUARD_ENVIRONMENT", "development").lower() != "production":
        origins.update(("http://localhost:5173", "http://127.0.0.1:5173"))
    return origins


def validate_production_settings():
    if os.getenv("VANGUARD_ENVIRONMENT", "development").lower() != "production":
        return
    if not os.getenv("VANGUARD_MASTER_KEY"):
        raise RuntimeError(
            "Production requires VANGUARD_MASTER_KEY from a secret manager"
        )
    from urllib.parse import urlsplit

    from cryptography.fernet import Fernet

    try:
        Fernet(os.environ["VANGUARD_MASTER_KEY"].encode())
    except ValueError as exc:
        raise RuntimeError("VANGUARD_MASTER_KEY is invalid") from exc
    if os.getenv("VANGUARD_SECURE_COOKIES", "true").lower() == "false":
        raise RuntimeError("Production requires secure session cookies")
    if os.getenv("VANGUARD_EXPOSE_RESET_TOKEN", "false").lower() == "true":
        raise RuntimeError("Production must not expose password reset tokens")
    public = urlsplit(os.getenv("VANGUARD_PUBLIC_URL", ""))
    if public.scheme != "https" or not public.netloc:
        raise RuntimeError("Production requires an HTTPS VANGUARD_PUBLIC_URL")


class APISecurityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))

        async def private_send(message):
            if message["type"] == "http.response.start":
                existing = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"cache-control"
                ]
                message = {
                    **message,
                    "headers": [
                        *existing,
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                    ],
                }
            await send(message)

        if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = headers.get(b"origin")
            if origin and origin.decode("latin1") not in allowed_origins():
                return await JSONResponse({"detail": "Untrusted request origin"}, 403)(
                    scope, receive, private_send
                )
            chunks, size = [], 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > MAX_BODY_BYTES:
                    return await JSONResponse(
                        {"detail": "Request body is too large"}, 413
                    )(scope, receive, private_send)
                chunks.append(chunk)
                if not message.get("more_body", False):
                    break
            body = b"".join(chunks)
            delivered = False
            original_receive = receive

            async def limited_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await original_receive()

            receive = limited_receive
        await self.app(scope, receive, private_send)
