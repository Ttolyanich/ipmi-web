"""Библиотека образов: список, загрузка по URL и загрузка из браузера.

Загрузка из браузера сделана возобновляемой: файлы здесь многогигабайтные, и
обрыв на 90% не должен означать «начинай сначала». Клиент режет файл на куски
и досылает их с указанием смещения; сервер принимает кусок, только если
смещение совпало с текущим размером недокачанного файла.
"""
import os
import secrets

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from .. import library
from ..audit import log as audit
from ..models import DownloadJob, db
from ..security import current_user, login_required

bp = Blueprint("library", __name__, url_prefix="/library")

CHUNK_UPLOAD_LIMIT = 64 * 1024 * 1024


@bp.route("/")
@login_required
def index():
    jobs = DownloadJob.query.order_by(DownloadJob.created_at.desc()).limit(20).all()
    return render_template("library.html", images=library.scan(), jobs=jobs)


@bp.route("/download", methods=["POST"])
@login_required
def download():
    url = (request.form.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        flash("Нужен http/https адрес образа", "error")
        return redirect(url_for("library.index"))

    filename = (request.form.get("filename") or "").strip() or url.rsplit("/", 1)[-1]
    job = library.start_download(url, filename, current_user().username)
    audit("iso_download", detail=f"{job.filename} из {url}")
    flash(f"Скачивание {job.filename} запущено", "ok")
    return redirect(url_for("library.index"))


@bp.route("/delete", methods=["POST"])
@login_required
def delete():
    filename = request.form.get("filename") or ""
    library.delete(filename)
    audit("iso_delete", detail=filename)
    flash(f"Образ {filename} удалён", "ok")
    return redirect(url_for("library.index"))


@bp.route("/hash", methods=["POST"])
@login_required
def rehash():
    filename = request.form.get("filename") or ""
    library.hash_async(library.safe_filename(filename))
    flash("Считаю SHA256, обнови страницу через минуту", "ok")
    return redirect(url_for("library.index"))


# --- возобновляемая загрузка -------------------------------------------


@bp.route("/upload/init", methods=["POST"])
@login_required
def upload_init():
    filename = library.safe_filename(request.json.get("filename", ""))
    size = int(request.json.get("size") or 0)

    if library.too_big(size):
        return (
            jsonify(
                error=(
                    f"Образ больше 4.7 ГБ — прошивка BMC такой не примет. "
                    f"Размер: {size / 1e9:.1f} ГБ"
                )
            ),
            400,
        )

    # Отказать сразу дешевле, чем оборвать заливку на девяноста процентах.
    available = library.free_space()
    if size and available < size * 1.05:
        return (
            jsonify(
                error=(
                    f"Не хватит места: нужно ~{size / 1e9:.1f} ГБ, "
                    f"свободно {available / 1e9:.1f} ГБ"
                )
            ),
            507,
        )

    upload_id = secrets.token_hex(8)
    open(os.path.join(library.upload_dir(), upload_id), "wb").close()
    return jsonify(upload_id=upload_id, filename=filename, offset=0)


@bp.route("/upload/<upload_id>/status")
@login_required
def upload_status(upload_id: str):
    path = _upload_path(upload_id)
    if path is None:
        return jsonify(error="загрузка не найдена"), 404
    return jsonify(offset=os.path.getsize(path))


@bp.route("/upload/<upload_id>", methods=["POST"])
@login_required
def upload_chunk(upload_id: str):
    path = _upload_path(upload_id)
    if path is None:
        return jsonify(error="загрузка не найдена"), 404

    offset = int(request.headers.get("X-Upload-Offset") or 0)
    current = os.path.getsize(path)
    if offset != current:
        # Клиент отстал или забежал вперёд — сообщаем реальное смещение,
        # он продолжит с него.
        return jsonify(error="смещение не совпало", offset=current), 409

    data = request.get_data(cache=False)
    if len(data) > CHUNK_UPLOAD_LIMIT:
        return jsonify(error="кусок слишком большой"), 413

    with open(path, "ab") as handle:
        handle.write(data)

    return jsonify(offset=os.path.getsize(path))


@bp.route("/upload/<upload_id>/finish", methods=["POST"])
@login_required
def upload_finish(upload_id: str):
    path = _upload_path(upload_id)
    if path is None:
        return jsonify(error="загрузка не найдена"), 404

    filename = library.safe_filename(request.json.get("filename", ""))
    target = os.path.join(library.iso_dir(), filename)
    os.replace(path, target)

    library.scan()
    library.hash_async(filename)
    audit("iso_upload", detail=f"{filename}, {os.path.getsize(target)} байт")
    return jsonify(ok=True, filename=filename)


def _upload_path(upload_id: str) -> str | None:
    # upload_id генерируем сами, но пришёл он из запроса — проверяем форму,
    # чтобы не выйти за пределы каталога.
    if not upload_id or not all(c in "0123456789abcdef" for c in upload_id):
        return None
    path = os.path.join(library.upload_dir(), upload_id)
    return path if os.path.isfile(path) else None
