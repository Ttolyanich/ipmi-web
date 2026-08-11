"""Жизненный цикл монтирования.

Последовательность важна и выстроена так, чтобы каждый шаг можно было
откатить: сначала создаётся одноразовая учётка, потом открывается окно в
файрволе, и только потом BMC получает команду. При сбое на любом шаге всё
сделанное до него отменяется — иначе в системе останутся висеть учётки и
открытые порты.

Окно живёт короткое время и продлевается фоновой задачей, пока BMC
подтверждает, что образ смонтирован. Фиксированный длинный TTL не годится:
истечение посреди установки ОС уронит её с невнятной ошибкой ввода-вывода.
"""
import logging

from flask import current_app

from . import firewall, smbusers
from .audit import log as audit
from .crypto import one_time_password, one_time_username
from .drivers import DriverError, get_driver
from .models import Mount, Server, db, utcnow

log = logging.getLogger(__name__)


class MountError(RuntimeError):
    pass


def active_mount(server_id: int) -> Mount | None:
    return (
        Mount.query.filter_by(server_id=server_id, state="active")
        .order_by(Mount.started_at.desc())
        .first()
    )


def start(server: Server, filename: str, username: str) -> Mount:
    if active_mount(server.id):
        raise MountError("на этом сервере уже есть активное монтирование")

    share_host = current_app.config["SHARE_HOST"]
    if not share_host:
        raise MountError(
            "не задан SHARE_HOST — BMC неоткуда забирать образ. "
            "Это адрес, по которому BMC видит наш SMB-сервер"
        )

    smb_user = one_time_username()
    smb_password = one_time_password()

    smbusers.create(smb_user, smb_password)
    window_open = False
    try:
        firewall.open_window(server.address)
        window_open = True

        driver = get_driver(server)
        path = driver.media_path(current_app.config["ISO_SHARE"], filename)
        driver.insert_media(share_host, path, smb_user, smb_password)
    except (DriverError, firewall.FirewallError) as exc:
        if window_open:
            _quiet(firewall.close_window, server.address)
        _quiet(smbusers.drop, smb_user)
        raise MountError(str(exc)) from exc

    mount = Mount(
        server_id=server.id,
        filename=filename,
        smb_user=smb_user,
        started_by=username,
        last_seen_at=utcnow(),
    )
    db.session.add(mount)
    db.session.commit()

    audit("mount", server.name, f"образ {filename}", username=username)
    return mount


def stop(mount: Mount, username: str = None, reason: str = None) -> None:
    server = mount.server
    try:
        get_driver(server).eject_media()
    except DriverError as exc:
        # Размонтирование в BMC могло не пройти, но убрать за собой учётку и
        # окно надо в любом случае: иначе они останутся висеть навсегда.
        log.warning("не удалось размонтировать на %s: %s", server.name, exc)
        mount.error = str(exc)

    _quiet(firewall.close_window, server.address)
    _quiet(smbusers.drop, mount.smb_user)

    mount.state = "closed"
    mount.ended_at = utcnow()
    db.session.commit()

    audit("unmount", server.name, reason or f"образ {mount.filename}", username=username)


def refresh(mount: Mount) -> bool:
    """Спросить у BMC, жив ли образ, и продлить окно, если жив.

    Возвращает True, если монтирование ещё активно. Источник правды — BMC:
    образ могли отцепить через его вебморду мимо нас.
    """
    server = mount.server
    try:
        mounted = get_driver(server).media_mounted()
    except DriverError as exc:
        # Недоступность BMC — не повод закрывать окно: сервер может быть в
        # процессе перезагрузки, а установка при этом идёт.
        log.warning("статус %s не получен: %s", server.name, exc)
        return True

    if not mounted:
        log.info("на %s образ больше не смонтирован, закрываем", server.name)
        stop(mount, reason="образ отцеплен мимо сервиса")
        return False

    try:
        firewall.open_window(server.address)
    except firewall.FirewallError as exc:
        log.error("окно для %s не продлено: %s", server.address, exc)

    mount.last_seen_at = utcnow()
    db.session.commit()
    return True


def poll_all(app) -> None:
    with app.app_context():
        for mount in Mount.query.filter_by(state="active").all():
            try:
                refresh(mount)
            except Exception:  # noqa: BLE001 — одна битая запись не должна ронять задачу
                log.exception("ошибка обхода монтирования #%s", mount.id)


def _quiet(func, *args) -> None:
    try:
        func(*args)
    except Exception:  # noqa: BLE001
        log.exception("ошибка при уборке: %s%s", func.__name__, args)
