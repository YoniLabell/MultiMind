"""Name + password accounts with cookie-based sessions."""

import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

import db

COOKIE_NAME = "mm_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days, matches db.get_session_user

_SCRYPT = {"n": 16384, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
        return secrets.compare_digest(digest.hex(), digest_hex)
    except (ValueError, AttributeError):
        return False


def _public_user(user: dict) -> dict:
    return {"id": user["id"], "name": user["username"]}


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


def _start_session(user: dict, request: Request, response: Response) -> None:
    token = db.create_session(user["id"])
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
    )


router = APIRouter(prefix="/auth")


class CredentialsRequest(BaseModel):
    name: str = ""
    password: str = ""


def _validated(body: CredentialsRequest) -> tuple[str, str]:
    name = body.name.strip()
    if not 2 <= len(name) <= 32:
        raise HTTPException(status_code=400, detail="Name must be 2-32 characters")
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    return name, body.password


@router.post("/register")
def register(body: CredentialsRequest, request: Request, response: Response):
    name, password = _validated(body)
    user = db.create_user(name, hash_password(password))
    if user is None:
        raise HTTPException(status_code=409, detail="That name is already taken")
    _start_session(user, request, response)
    return {"user": _public_user(user)}


@router.post("/login")
def login(body: CredentialsRequest, request: Request, response: Response):
    name = body.name.strip()
    user = db.get_user_by_username(name)
    if user is None or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Wrong name or password")
    _start_session(user, request, response)
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
