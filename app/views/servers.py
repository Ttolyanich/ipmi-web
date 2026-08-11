from flask import Blueprint, flash, redirect, render_template, request, url_for

from .. import firewall, library, mounts, smbusers
from ..audit import log as audit
from ..crypto import encrypt
from ..drivers import VENDORS, DriverError, get_driver
from ..models import Mount, Server, db
from ..security import admin_required, current_user, login_required

bp = Blueprint("servers", __name__)


@bp.route("/servers")
@login_required
def index():
    servers = Server.query.order_by(Server.name).all()
    active = {m.server_id: m for m in Mount.query.filter_by(state="active").all()}
    return render_template("servers.html", servers=servers, active=active)


@bp.route("/servers/<int:server_id>")
@login_required
def detail(server_id: int):
    server = db.session.get(Server, server_id) or _missing()
    images = library.scan()
    mount = mounts.active_mount(server.id)

    # Статус спрашиваем у BMC, а не у своей базы: образ могли отцепить через
    # вебморду BMC мимо сервиса.
    status, power, error = None, None, None
    try:
        driver = get_driver(server)
        status = driver.media_status()
        power = driver.power_state()
    except DriverError as exc:
        error = str(exc)

    history = (
        server.mounts.order_by(Mount.started_at.desc()).limit(10).all()
    )
    firewall_code, firewall_hint = firewall.status()
    return render_template(
        "server_detail.html",
        server=server,
        images=images,
        mount=mount,
        slots=status,
        power=power,
        error=error,
        history=history,
        firewall_code=firewall_code,
        firewall_hint=firewall_hint,
    )


@bp.route("/servers/new", methods=["GET", "POST"])
@admin_required
def create():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        address = (request.form.get("address") or "").strip()
        password = request.form.get("bmc_password") or ""

        if not name or not address or not password:
            flash("Имя, адрес и пароль обязательны", "error")
        elif Server.query.filter_by(name=name).first():
            flash("Сервер с таким именем уже есть", "error")
        else:
            server = Server(
                name=name,
                address=address,
                vendor=request.form.get("vendor") or "supermicro",
                bmc_user=(request.form.get("bmc_user") or "ADMIN").strip(),
                bmc_password_enc=encrypt(password),
                console_url=(request.form.get("console_url") or "").strip() or None,
                notes=(request.form.get("notes") or "").strip() or None,
            )
            db.session.add(server)
            db.session.commit()
            audit("server_create", server.name, f"адрес {server.address}")
            return redirect(url_for("servers.detail", server_id=server.id))

    return render_template("server_form.html", server=None, vendors=VENDORS)


@bp.route("/servers/<int:server_id>/edit", methods=["GET", "POST"])
@admin_required
def edit(server_id: int):
    server = db.session.get(Server, server_id) or _missing()

    if request.method == "POST":
        server.name = (request.form.get("name") or server.name).strip()
        server.address = (request.form.get("address") or server.address).strip()
        server.vendor = request.form.get("vendor") or server.vendor
        server.bmc_user = (request.form.get("bmc_user") or server.bmc_user).strip()
        server.console_url = (request.form.get("console_url") or "").strip() or None
        server.notes = (request.form.get("notes") or "").strip() or None

        # Пустое поле пароля означает «оставить прежний».
        password = request.form.get("bmc_password") or ""
        if password:
            server.bmc_password_enc = encrypt(password)

        db.session.commit()
        audit("server_edit", server.name)
        return redirect(url_for("servers.detail", server_id=server.id))

    return render_template("server_form.html", server=server, vendors=VENDORS)


@bp.route("/servers/<int:server_id>/delete", methods=["POST"])
@admin_required
def delete(server_id: int):
    server = db.session.get(Server, server_id) or _missing()
    if mounts.active_mount(server.id):
        flash("Сначала размонтируй образ", "error")
        return redirect(url_for("servers.detail", server_id=server.id))

    name = server.name
    Mount.query.filter_by(server_id=server.id).delete()
    db.session.delete(server)
    db.session.commit()
    audit("server_delete", name)
    return redirect(url_for("servers.index"))


# --- операции с железом -------------------------------------------------


@bp.route("/servers/<int:server_id>/mount", methods=["POST"])
@login_required
def mount(server_id: int):
    server = db.session.get(Server, server_id) or _missing()
    filename = request.form.get("filename") or ""

    try:
        mounts.start(server, filename, current_user().username)
        flash(f"Образ {filename} смонтирован", "ok")
    except (mounts.MountError, DriverError, smbusers.SmbUserError) as exc:
        # Ловим только предвидимые причины: ошибка в самом коде должна
        # долетать до журнала со стеком, а не превращаться в розовую плашку.
        flash(f"Не удалось смонтировать: {exc}", "error")

    return redirect(url_for("servers.detail", server_id=server.id))


@bp.route("/servers/<int:server_id>/unmount", methods=["POST"])
@login_required
def unmount(server_id: int):
    server = db.session.get(Server, server_id) or _missing()
    mount_row = mounts.active_mount(server.id)

    if mount_row is None:
        # Активной записи нет, но образ мог быть смонтирован мимо сервиса —
        # честно пробуем отцепить его в BMC.
        try:
            get_driver(server).eject_media()
            audit("unmount", server.name, "монтирование вне сервиса")
            flash("Образ отцеплен", "ok")
        except DriverError as exc:
            flash(f"Не удалось отцепить: {exc}", "error")
    else:
        mounts.stop(mount_row, current_user().username)
        flash("Образ отцеплен", "ok")

    return redirect(url_for("servers.detail", server_id=server.id))


@bp.route("/servers/<int:server_id>/boot", methods=["POST"])
@login_required
def boot_override(server_id: int):
    server = db.session.get(Server, server_id) or _missing()
    target = request.form.get("target") or "cdrom"

    try:
        get_driver(server).set_boot_override(target)
        audit("boot_override", server.name, f"однократно с {target}")
        flash(f"Следующая загрузка — с {target}, однократно", "ok")
    except DriverError as exc:
        flash(str(exc), "error")

    return redirect(url_for("servers.detail", server_id=server.id))


@bp.route("/servers/<int:server_id>/power", methods=["POST"])
@login_required
def power(server_id: int):
    server = db.session.get(Server, server_id) or _missing()
    action = request.form.get("action") or ""

    try:
        get_driver(server).power_action(action)
        audit("power", server.name, action)
        flash(f"Питание: {action}", "ok")
    except DriverError as exc:
        flash(str(exc), "error")

    return redirect(url_for("servers.detail", server_id=server.id))


def _missing():
    from flask import abort

    abort(404)
