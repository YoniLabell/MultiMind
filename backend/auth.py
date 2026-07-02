"""Google Sign-In and cookie-based sessions."""

import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

import db

COOKIE_NAME = "mm_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days, matches db.get_session_user


def verify_google_token(credential: str) -> dict:
    """Verify a Google Identity Services ID token and return its claims."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    if not client_id:
        raise HTTPException(status_code=503, detail="GOOGLE_CLIENT_ID is not configured")
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    try:
        return id_token.verify_oauth2_token(credential, google_requests.Request(), client_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid Google credential: {exc}")


def _public_user(user: dict) -> dict:
    return {"id": user["id"], "email": user["email"], "name": user["name"], "picture": user["picture"]}


def require_user(request: Request) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    user = db.get_session_user(token) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user


def _cookie_secure(request: Request) -> bool:
    return (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto", "").startswith("https")
    )


router = APIRouter(prefix="/auth")


class GoogleLoginRequest(BaseModel):
    credential: str


@router.get("/config")
def auth_config():
    return {"google_client_id": os.environ.get("GOOGLE_CLIENT_ID")}


@router.post("/google")
def login_with_google(body: GoogleLoginRequest, request: Request, response: Response):
    claims = verify_google_token(body.credential)
    user = db.upsert_user(
        claims["sub"], claims.get("email"), claims.get("name"), claims.get("picture")
    )
    token = db.create_session(user["id"])
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
    )
    return {"user": _public_user(user)}


@router.get("/me")
def me(user: dict = Depends(require_user)):
    return {"user": _public_user(user)}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        db.delete_session(token)
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}
