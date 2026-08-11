"""Сборка приложения."""
import atexit
import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, redirect, url_for

from .config import Config
from .models import User, db
from .security import csrf_protect, csrf_token, current_user, hash_password

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def create_app(config_object=Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object)

    if not app.config["SECRET_KEY"]:
        raise RuntimeError("SECRET_KEY не задан — сессии подписывать нечем")

    os.makedirs(app.config["DATA_DIR"], exist_ok=True)
    db.init_app(app)

    with app.app_context():
        db.create_all()
        _bootstrap_admin(app)

    _register_blueprints(app)
    _register_helpers(app)

    if os.environ.get("ENABLE_SCHEDULER", "1") == "1":
        _start_scheduler(app)

    return app


def _bootstrap_admin(app: Flask) -> None:
    """Первый администратор создаётся из окружения.

    Дефолтных admin/admin123 здесь нет и не будет: сервис хранит пароли от всех
    BMC, а это доступ ниже уровня ОС.
    """
    if User.query.first() is not None:
        return

    username = app.config["BOOTSTRAP_ADMIN"]
    password = app.config["BOOTSTRAP_PASSWORD"]
    if not username or not password:
        log.warning(
            "Пользователей нет, а BOOTSTRAP_ADMIN/BOOTSTRAP_PASSWORD не заданы — "
            "войти будет невозможно. Задай их в .env и перезапусти."
        )
        return

    db.session.add(
        User(username=username, password_hash=hash_password(password), is_admin=True)
    )
    db.session.commit()
    log.info("создан первый администратор %s", username)


def _register_blueprints(app: Flask) -> None:
    from .views import audit_log, auth, library, servers

    app.register_blueprint(auth.bp)
    app.register_blueprint(servers.bp)
    app.register_blueprint(library.bp)
    app.register_blueprint(audit_log.bp)

    @app.route("/")
    def index():
        return redirect(url_for("servers.index"))


def _register_helpers(app: Flask) -> None:
    app.before_request(csrf_protect)

    @app.context_processor
    def inject():
        return {"csrf_token": csrf_token, "user": current_user()}

    @app.template_filter("bytes")
    def human_bytes(value):
        if not value:
            return "—"
        size = float(value)
        for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
            if size < 1024 or unit == "ТБ":
                return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
            size /= 1024
        return value

    @app.template_filter("moment")
    def moment(value):
        return value.strftime("%d.%m.%Y %H:%M") if value else "—"


def _start_scheduler(app: Flask) -> None:
    """Опрос BMC и продление окон в файрволе.

    Работает внутри процесса, как в остальном парке. Запускать gunicorn нужно
    в один воркер, иначе задача будет дублироваться.
    """
    from . import mounts

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        mounts.poll_all,
        "interval",
        seconds=app.config["MOUNT_POLL_SECONDS"],
        args=[app],
        id="poll_mounts",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    atexit.register(scheduler.shutdown, wait=False)
    log.info("планировщик запущен, опрос каждые %s с", app.config["MOUNT_POLL_SECONDS"])
