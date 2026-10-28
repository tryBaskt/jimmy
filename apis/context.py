"""Shared identity and project ownership enforcement for HTTP boundaries."""

from functools import lru_cache
import time
import jwt
from fastapi import HTTPException, Request


@lru_cache
def jwks(issuer):
    return jwt.PyJWKClient(issuer + "/.well-known/jwks.json", cache_keys=True)


class ServiceContext:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store

    def user(self, request: Request):
        settings = self.settings
        if settings.mode == "local":
            host = request.client.host if request.client else ""
            if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
                raise HTTPException(403, "Development identity requires loopback")
            return {"sub": "local-developer", "exp": int(time.time()) + 3600}
        try:
            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                raise ValueError()
            token = header[7:]
            key = jwks(settings.issuer).get_signing_key_from_jwt(token).key
            payload = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=settings.issuer,
                audience=settings.audience,
                options={"require": ["exp", "iat", "sub"]},
            )
            if payload.get("token_use") != "id":
                raise ValueError("Expected Cognito ID token")
            return payload
        except (jwt.PyJWTError, ValueError):
            raise HTTPException(401, "Valid sign-in required") from None

    def project(self, pid, owner):
        item, _ = self.store.get(f"project#{pid}", "meta")
        if not item or item["owner"] != owner:
            raise HTTPException(404, "Project not found")
        return item
