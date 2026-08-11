"""Авторизация, CSRF и ограничение попыток входа.

Авторизация своя, не SSO — сознательное решение. Из него следует требование:
пользователи именные, иначе аудит-журнал бессмысленен ("кто-то в 03:41
переустановил боевой сервер" не стоит ничего).
"""
import functools
import hmac
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from flask import current_app, flash, g, redirect, request, session, url_for

from .models import LoginAttempt, User, db, utcnow

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def client_ip() -> str:
    # За nginx реальный адрес приходит заголовком; сервис не публикуется в
    # интернет напрямую, поэтому доверять ему здесь допустимо.
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "-"


def login_blocked(ip: str) -> bool:
    window = utcnow() - timedelta(seconds=current_app.config["LOGIN_WINDOW_SECONDS"])
    failures = LoginAttempt.query.filter(
        LoginAttempt.ip == ip,
        LoginAttempt.ts >= window,
        LoginAttempt.ok.is_(False),
    ).count()
    return failures >= current_app.config["LOGIN_MAX_ATTEMPTS"]


def record_attempt(ip: str, username: str, ok: bool) -> None:
    db.session.add(LoginAttempt(ip=ip, username=username, ok=ok))
    db.session.commit()


def current_user():
    if "user_id" not in session:
        return None
    if getattr(g, "_user", None) is None:
        g._user = db.session.get(User, session["user_id"])
    return g._user


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None or user.disabled:
            session.clear()
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @functools.wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user().is_admin:
            flash("Действие доступно только администратору", "error")
            return redirect(url_for("servers.index"))
        return view(*args, **kwargs)

    return wrapped


# --- CSRF ---------------------------------------------------------------
# В парке CSRF-защиты нет нигде; здесь она обязательна, потому что этот сервис
# умеет перезагружать боевые серверы одним POST-запросом.


def csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def csrf_protect() -> None:
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf_token", "")
    if not expected or not hmac.compare_digest(sent, expected):
        from flask import abort

        abort(400, "CSRF-токен не совпал")
