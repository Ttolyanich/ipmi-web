"""Библиотека образов.

Источник правды — сам каталог: файл, положенный через scp, появится в списке
сам. База хранит только то, что дорого считать заново (SHA256).

Тот же каталог отдаётся по SMB как шара — библиотека одна, протокол один.
"""
import hashlib
import logging
import os
import re
import threading
import time

import requests
from flask import current_app

from .models import DownloadJob, IsoFile, db

log = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
UPLOAD_SUBDIR = ".uploads"


def safe_filename(name: str) -> str:
    name = os.path.basename(name or "").strip()
    name = _SAFE_NAME.sub("_", name)
    return name.lstrip(".") or "image.iso"


def iso_dir() -> str:
    path = current_app.config["ISO_DIR"]
    os.makedirs(path, exist_ok=True)
    return path


def upload_dir() -> str:
    path = os.path.join(iso_dir(), UPLOAD_SUBDIR)
    os.makedirs(path, exist_ok=True)
    return path


def free_space() -> int:
    stat = os.statvfs(iso_dir())
    return stat.f_bavail * stat.f_frsize


def purge_stale_uploads(app, max_age_hours: int = 48) -> None:
    """Убрать брошенные незавершённые загрузки и старые записи о входах.

    Без этого каждая прерванная заливка навсегда занимает несколько гигабайт,
    и однажды диск кончится ровно в момент, когда нужен новый образ.
    """
    from datetime import timedelta

    from .models import LoginAttempt, db, utcnow

    with app.app_context():
        directory = upload_dir()
        deadline = time.time() - max_age_hours * 3600
        for entry in os.scandir(directory):
            if entry.is_file() and entry.stat().st_mtime < deadline:
                os.remove(entry.path)
                log.info("удалён брошенный кусок загрузки %s", entry.name)

        days = current_app.config.get("LOGIN_ATTEMPT_RETENTION_DAYS", 7)
        removed = LoginAttempt.query.filter(
            LoginAttempt.ts < utcnow() - timedelta(days=days)
        ).delete()
        if removed:
            db.session.commit()
            log.info("удалено записей о попытках входа: %s", removed)


def too_big(size: int) -> bool:
    """Прошивка Supermicro отказывается монтировать образы больше 4.7 ГБ.

    Проверять надо здесь, а не при монтировании: иначе пользователь узнает об
    этом посреди установки, по невнятной ошибке ввода-вывода.
    """
    return size > current_app.config["MAX_IMAGE_BYTES"]


def scan() -> list[dict]:
    """Список образов. Метаданные подтягиваются из базы, отсутствующие
    записи создаются, устаревшие (файл изменился) — обнуляются."""
    directory = iso_dir()
    cached = {row.filename: row for row in IsoFile.query.all()}
    seen = set()
    images = []

    for entry in sorted(os.scandir(directory), key=lambda e: e.name.lower()):
        # .part — недокачанный файл. Показывать его нельзя: он выглядит как
        # обычный образ, но смонтируется половина дистрибутива, и выяснится
        # это уже во время установки.
        if not entry.is_file() or entry.name.startswith(".") or entry.name.endswith(".part"):
            continue
        stat = entry.stat()
        seen.add(entry.name)
        row = cached.get(entry.name)

        if row is None:
            row = IsoFile(filename=entry.name, size=stat.st_size, mtime=stat.st_mtime)
            db.session.add(row)
        elif row.size != stat.st_size or abs(row.mtime - stat.st_mtime) > 1:
            row.size, row.mtime, row.sha256 = stat.st_size, stat.st_mtime, None

        images.append(
            {
                "filename": entry.name,
                "size": stat.st_size,
                "sha256": row.sha256,
                "too_big": too_big(stat.st_size),
            }
        )

    for name, row in cached.items():
        if name not in seen:
            db.session.delete(row)

    db.session.commit()
    return images


def delete(filename: str) -> None:
    filename = safe_filename(filename)
    path = os.path.join(iso_dir(), filename)
    if os.path.isfile(path):
        os.remove(path)
    row = IsoFile.query.filter_by(filename=filename).first()
    if row:
        db.session.delete(row)
        db.session.commit()


def compute_sha256(app, filename: str) -> None:
    with app.app_context():
        path = os.path.join(current_app.config["ISO_DIR"], filename)
        if not os.path.isfile(path):
            return
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
        row = IsoFile.query.filter_by(filename=filename).first()
        if row:
            row.sha256 = digest.hexdigest()
            db.session.commit()
        log.info("SHA256 посчитан для %s", filename)


def hash_async(filename: str) -> None:
    app = current_app._get_current_object()
    threading.Thread(target=compute_sha256, args=(app, filename), daemon=True).start()


# --- скачивание по URL --------------------------------------------------


def _download(app, job_id: int) -> None:
    with app.app_context():
        job = db.session.get(DownloadJob, job_id)
        if job is None:
            return
        target = os.path.join(current_app.config["ISO_DIR"], job.filename)
        partial = target + ".part"
        job.status = "running"
        db.session.commit()

        try:
            with requests.get(job.url, stream=True, timeout=60) as response:
                response.raise_for_status()
                job.total = int(response.headers.get("Content-Length") or 0)
                db.session.commit()

                # Проверяем место до записи: иначе диск кончится на середине,
                # и разбираться придётся по невнятной ошибке ввода-вывода.
                available = free_space()
                if job.total and available < job.total * 1.05:
                    raise RuntimeError(
                        f"не хватит места: нужно ~{job.total / 1e9:.1f} ГБ, "
                        f"свободно {available / 1e9:.1f} ГБ"
                    )

                written = 0
                with open(partial, "wb") as handle:
                    for chunk in response.iter_content(1024 * 1024):
                        handle.write(chunk)
                        written += len(chunk)
                        # Прогресс пишем не на каждый мегабайт, чтобы не
                        # долбить sqlite на многогигабайтных файлах.
                        if written % (32 * 1024 * 1024) < 1024 * 1024:
                            job.downloaded = written
                            db.session.commit()

            # Оборванная отдача даёт файл, который выглядит целым. Без этой
            # проверки половина дистрибутива попадёт в библиотеку как готовый
            # образ, и выяснится это во время установки.
            if job.total and written != job.total:
                raise RuntimeError(
                    f"файл получен не полностью: {written} из {job.total} байт"
                )
            if not job.total:
                log.warning("сервер не сообщил размер %s, полноту проверить нечем", job.url)

            os.replace(partial, target)
            job.downloaded = written
            job.status = "done"
            db.session.commit()
            log.info("образ %s скачан (%d байт)", job.filename, written)
            compute_sha256(app, job.filename)
        except Exception as exc:  # noqa: BLE001 — в журнал уходит любая причина
            if os.path.exists(partial):
                os.remove(partial)
            job.status = "error"
            job.error = str(exc)
            db.session.commit()
            log.exception("не удалось скачать %s", job.url)


def start_download(url: str, filename: str, username: str) -> DownloadJob:
    job = DownloadJob(url=url, filename=safe_filename(filename), created_by=username)
    db.session.add(job)
    db.session.commit()

    app = current_app._get_current_object()
    threading.Thread(target=_download, args=(app, job.id), daemon=True).start()
    return job
