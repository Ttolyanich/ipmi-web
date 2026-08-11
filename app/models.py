"""Модель данных.

Важное решение из DECISIONS.md: база хранит намерение и историю, но НЕ является
источником правды о том, что сейчас смонтировано. Правду знает BMC, её
опрашивают. Иначе неизбежно расхождение, когда кто-то размонтирует образ через
вебморду BMC мимо сервиса.
"""
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    disabled = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    last_login_at = db.Column(db.DateTime)


class Server(db.Model):
    __tablename__ = "servers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), unique=True, nullable=False)
    address = db.Column(db.String(128), nullable=False)
    vendor = db.Column(db.String(32), default="supermicro", nullable=False)
    bmc_user = db.Column(db.String(64), nullable=False)
    bmc_password_enc = db.Column(db.LargeBinary, nullable=False)
    console_url = db.Column(db.String(255))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    mounts = db.relationship("Mount", back_populates="server", lazy="dynamic")

    @property
    def console_link(self) -> str:
        return self.console_url or f"https://{self.address}/"


class IsoFile(db.Model):
    """Кеш метаданных образа. Источник правды — сам каталог; сюда пишется
    только то, что дорого считать заново (SHA256)."""

    __tablename__ = "iso_files"

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), unique=True, nullable=False)
    size = db.Column(db.BigInteger, nullable=False)
    mtime = db.Column(db.Float, nullable=False)
    sha256 = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class DownloadJob(db.Model):
    __tablename__ = "download_jobs"

    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.Text, nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(16), default="pending", nullable=False)  # pending|running|done|error
    downloaded = db.Column(db.BigInteger, default=0, nullable=False)
    total = db.Column(db.BigInteger, default=0, nullable=False)
    error = db.Column(db.Text)
    created_by = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    @property
    def percent(self) -> int:
        if not self.total:
            return 0
        return min(100, int(self.downloaded * 100 / self.total))


class Mount(db.Model):
    __tablename__ = "mounts"

    id = db.Column(db.Integer, primary_key=True)
    server_id = db.Column(db.Integer, db.ForeignKey("servers.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    state = db.Column(db.String(16), default="active", nullable=False)  # active|closed|failed
    smb_user = db.Column(db.String(64), nullable=False)
    started_by = db.Column(db.String(64))
    started_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    ended_at = db.Column(db.DateTime)
    last_seen_at = db.Column(db.DateTime)
    error = db.Column(db.Text)

    server = db.relationship("Server", back_populates="mounts")


class AuditEntry(db.Model):
    __tablename__ = "audit"

    id = db.Column(db.Integer, primary_key=True)
    ts = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    username = db.Column(db.String(64))
    ip = db.Column(db.String(64))
    action = db.Column(db.String(64), nullable=False)
    server_name = db.Column(db.String(128))
    detail = db.Column(db.Text)


class LoginAttempt(db.Model):
    __tablename__ = "login_attempts"

    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(64), index=True)
    username = db.Column(db.String(64))
    ts = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    ok = db.Column(db.Boolean, default=False, nullable=False)
