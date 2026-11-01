import os
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)


@lru_cache
def keys(issuer):
    return jwt.PyJWKClient(issuer + "/.well-known/jwks.json", cache_jwk_set=True, lifespan=3600)


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    issuer, audience = os.getenv("COGNITO_ISSUER"), os.getenv("COGNITO_CLIENT_ID")
    if not issuer or not audience:
        raise HTTPException(503, "Cognito authentication is not configured")
    if not credentials:
        raise HTTPException(401, "Sign in to continue", headers={"WWW-Authenticate": "Bearer"})
    try:
        key = keys(issuer).get_signing_key_from_jwt(credentials.credentials)
        claims = jwt.decode(credentials.credentials, key.key, algorithms=["RS256"],
                            issuer=issuer, audience=audience,
                            options={"require": ["exp", "iat", "sub", "token_use"]})
        if claims["token_use"] != "id":
            raise jwt.InvalidTokenError("Expected a Cognito identity token")
        return claims["sub"]
    except jwt.PyJWTError:
        raise HTTPException(401, "Sign-in expired or invalid", headers={"WWW-Authenticate": "Bearer"}) from None
