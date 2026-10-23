"""Cognito login backed by opaque, server-side browser sessions."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.config import Settings

router = APIRouter(prefix="/auth", tags=["auth"])
SESSION_COOKIE = "__Host-jimmy-session"
STATE_COOKIE = "__Secure-jimmy-oauth-state"
SESSION_SECONDS = 30 * 24 * 60 * 60


def _configured(settings: Settings) -> None:
    if not all((settings.cognito_domain, settings.cognito_client_id,
                settings.public_url, settings.sessions_table)):
        raise HTTPException(status_code=503, detail="Sign-in is not configured")


def _table(settings: Settings):
    return boto3.Session(region_name=settings.aws_region).resource("dynamodb").Table(settings.sessions_table)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def _cognito_form(settings: Settings, path: str, values: dict[str, str]) -> dict:
    request = UrlRequest(
        f"{settings.cognito_domain.rstrip('/')}{path}",
        data=urlencode(values).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=8) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except HTTPError as error:
        if error.code == 400:
            raise HTTPException(status_code=401, detail="Cognito session expired") from error
        raise HTTPException(status_code=502, detail="Cognito sign-in failed") from error
    except URLError as error:
        raise HTTPException(status_code=502, detail="Cognito sign-in failed") from error


def _userinfo(settings: Settings, access_token: str) -> dict:
    request = UrlRequest(
        f"{settings.cognito_domain.rstrip('/')}/oauth2/userInfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        with urlopen(request, timeout=8) as response:
            return json.load(response)
    except (HTTPError, URLError) as error:
        raise HTTPException(status_code=502, detail="Could not verify Cognito user") from error


def _start(request: Request, signup: bool) -> RedirectResponse:
    settings: Settings = request.app.state.settings
    _configured(settings)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    _table(settings).put_item(Item={
        "session_id": f"oauth#{_digest(state)}",
        "code_verifier": verifier,
        "expires_at": int(time.time()) + 600,
    })
    query = urlencode({
        "client_id": settings.cognito_client_id,
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": f"{settings.public_url.rstrip('/')}/api/auth/callback",
        "state": state,
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
    })
    path = "signup" if signup else "login"
    response = RedirectResponse(f"{settings.cognito_domain.rstrip('/')}/{path}?{query}", status_code=302)
    response.set_cookie(STATE_COOKIE, state, max_age=600, secure=True, httponly=True,
                        samesite="lax", path="/api/auth")
    return response


@router.get("/login")
def login(request: Request) -> RedirectResponse:
    return _start(request, False)


@router.get("/signup")
def signup(request: Request) -> RedirectResponse:
    return _start(request, True)


@router.get("/callback")
def callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
    settings: Settings = request.app.state.settings
    _configured(settings)
    cookie_state = request.cookies.get(STATE_COOKIE, "")
    if not code or not state or not secrets.compare_digest(state, cookie_state):
        raise HTTPException(status_code=400, detail="Invalid sign-in callback")
    table = _table(settings)
    key = {"session_id": f"oauth#{_digest(state)}"}
    pending = table.get_item(Key=key, ConsistentRead=True).get("Item")
    if not pending or pending["expires_at"] <= int(time.time()):
        raise HTTPException(status_code=400, detail="Sign-in expired")
    table.delete_item(Key=key, ConditionExpression="attribute_exists(session_id)")
    tokens = _cognito_form(settings, "/oauth2/token", {
        "grant_type": "authorization_code",
        "client_id": settings.cognito_client_id,
        "code": code,
        "code_verifier": pending["code_verifier"],
        "redirect_uri": f"{settings.public_url.rstrip('/')}/api/auth/callback",
    })
    identity = _userinfo(settings, tokens["access_token"])
    if not identity.get("sub") or not tokens.get("refresh_token"):
        raise HTTPException(status_code=502, detail="Cognito returned incomplete credentials")
    session_id = secrets.token_urlsafe(48)
    now = int(time.time())
    table.put_item(Item={
        "session_id": _digest(session_id),
        "user_sub": identity["sub"],
        "email": identity.get("email", ""),
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "access_expires_at": now + int(tokens["expires_in"]),
        "expires_at": now + SESSION_SECONDS,
    })
    response = RedirectResponse(f"{settings.public_url.rstrip('/')}/", status_code=303)
    response.delete_cookie(STATE_COOKIE, path="/api/auth")
    response.set_cookie(SESSION_COOKIE, session_id, max_age=SESSION_SECONDS,
                        secure=True, httponly=True, samesite="lax", path="/")
    return response


def current_session(request: Request) -> dict:
    settings: Settings = request.app.state.settings
    _configured(settings)
    session_id = request.cookies.get(SESSION_COOKIE)
    if not session_id:
        raise HTTPException(status_code=401, detail="Sign in required")
    table = _table(settings)
    key = {"session_id": _digest(session_id)}
    item = table.get_item(Key=key, ConsistentRead=True).get("Item")
    now = int(time.time())
    if not item or item["expires_at"] <= now:
        raise HTTPException(status_code=401, detail="Session expired")
    if item["access_expires_at"] <= now + 60:
        lease = now + 15
        try:
            table.update_item(
                Key=key,
                UpdateExpression="SET refresh_lock_until = :lease",
                ConditionExpression="attribute_not_exists(refresh_lock_until) OR refresh_lock_until < :now",
                ExpressionAttributeValues={":lease": lease, ":now": now},
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            for _ in range(20):
                time.sleep(0.2)
                updated = table.get_item(Key=key, ConsistentRead=True).get("Item")
                if updated and updated["access_expires_at"] > int(time.time()) + 60:
                    return updated
            raise HTTPException(status_code=503, detail="Session refresh is busy") from error
        try:
            tokens = _cognito_form(settings, "/oauth2/token", {
                "grant_type": "refresh_token",
                "client_id": settings.cognito_client_id,
                "refresh_token": item["refresh_token"],
            })
            item["access_token"] = tokens["access_token"]
            item["refresh_token"] = tokens.get("refresh_token", item["refresh_token"])
            item["access_expires_at"] = int(time.time()) + int(tokens["expires_in"])
            table.update_item(
                Key=key,
                UpdateExpression="SET access_token = :access, refresh_token = :refresh, access_expires_at = :expiry REMOVE refresh_lock_until",
                ConditionExpression="refresh_lock_until = :lease",
                ExpressionAttributeValues={
                    ":access": item["access_token"], ":refresh": item["refresh_token"],
                    ":expiry": item["access_expires_at"], ":lease": lease,
                },
            )
        except HTTPException as error:
            if error.status_code == 401:
                table.delete_item(Key=key)
            else:
                try:
                    table.update_item(
                        Key=key, UpdateExpression="REMOVE refresh_lock_until",
                        ConditionExpression="refresh_lock_until = :lease",
                        ExpressionAttributeValues={":lease": lease},
                    )
                except ClientError:
                    pass
            raise
        except Exception:
            try:
                table.update_item(
                    Key=key, UpdateExpression="REMOVE refresh_lock_until",
                    ConditionExpression="refresh_lock_until = :lease",
                    ExpressionAttributeValues={":lease": lease},
                )
            except ClientError:
                pass
            raise
    return item


def require_same_origin(request: Request) -> None:
    expected = request.app.state.settings.public_url.rstrip("/")
    if not expected or request.headers.get("origin") != expected:
        raise HTTPException(status_code=403, detail="Invalid request origin")


@router.get("/session")
def session(request: Request) -> dict:
    user = current_session(request)
    return {"sub": user["user_sub"], "email": user["email"]}


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    require_same_origin(request)
    settings: Settings = request.app.state.settings
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id and settings.sessions_table:
        table = _table(settings)
        key = {"session_id": _digest(session_id)}
        item = table.get_item(Key=key, ConsistentRead=True).get("Item")
        table.delete_item(Key=key)
        if item and settings.cognito_client_id and settings.cognito_domain:
            try:
                _cognito_form(settings, "/oauth2/revoke", {
                    "client_id": settings.cognito_client_id,
                    "token": item["refresh_token"],
                })
            except HTTPException:
                pass
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
