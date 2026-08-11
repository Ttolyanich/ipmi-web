from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..audit import log as audit
from ..models import User, db, utcnow
from ..security import (
    admin_required,
    client_ip,
    current_user,
    hash_password,
    login_blocked,
    login_required,
    record_attempt,
    verify_password,
)

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("servers.index"))

    if request.method == "POST":
        ip = client_ip()
        if login_blocked(ip):
            flash("Слишком много неудачных попыток. Подожди несколько минут.", "error")
            return render_template("login.html"), 429

        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = User.query.filter_by(username=username).first()

        if user and not user.disabled and verify_password(user.password_hash, password):
            record_attempt(ip, username, True)
            session.clear()
            session["user_id"] = user.id
            session.permanent = True
            user.last_login_at = utcnow()
            db.session.commit()
            audit("login", username=username)
            return redirect(request.args.get("next") or url_for("servers.index"))

        record_attempt(ip, username, False)
        flash("Неверный логин или пароль", "error")

    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    audit("logout")
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/users")
@admin_required
def users():
    return render_template("users.html", users=User.query.order_by(User.username).all())


@bp.route("/users/create", methods=["POST"])
@admin_required
def create_user():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    if len(username) < 3 or len(password) < 10:
        flash("Логин от 3 символов, пароль от 10", "error")
    elif User.query.filter_by(username=username).first():
        flash("Такой пользователь уже есть", "error")
    else:
        db.session.add(
            User(
                username=username,
                password_hash=hash_password(password),
                is_admin=bool(request.form.get("is_admin")),
            )
        )
        db.session.commit()
        audit("user_create", detail=username)
        flash(f"Пользователь {username} создан", "ok")

    return redirect(url_for("auth.users"))


@bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@admin_required
def toggle_user(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        flash("Пользователь не найден", "error")
    elif user.id == current_user().id:
        flash("Себя отключить нельзя", "error")
    else:
        user.disabled = not user.disabled
        db.session.commit()
        audit("user_disable" if user.disabled else "user_enable", detail=user.username)

    return redirect(url_for("auth.users"))


@bp.route("/users/<int:user_id>/password", methods=["POST"])
@admin_required
def reset_password(user_id: int):
    user = db.session.get(User, user_id)
    password = request.form.get("password") or ""
    if user is None:
        flash("Пользователь не найден", "error")
    elif len(password) < 10:
        flash("Пароль от 10 символов", "error")
    else:
        user.password_hash = hash_password(password)
        db.session.commit()
        audit("user_password", detail=user.username)
        flash("Пароль изменён", "ok")

    return redirect(url_for("auth.users"))
