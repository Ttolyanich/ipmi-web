"""Клиент к хостовому демону, управляющему окном в nftables.

Почему через демон, а не напрямую: веб-морда хранит пароли от всех BMC, и
давать ей NET_ADMIN на хосте — значит превратить её компрометацию в контроль
над файрволом. Демон умеет ровно две операции.

Почему множество с таймаутом, а не просто правило: ядро удаляет элемент само,
поэтому падение сервиса или контейнера закрывает порт, а не оставляет его
открытым.
"""
import logging
import socket

from flask import current_app

log = logging.getLogger(__name__)


class FirewallError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(current_app.config.get("FIREWALL_ENABLED", True))


def _send(command: str) -> str:
    path = current_app.config["FW_SOCKET"]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(10)
            sock.connect(path)
            sock.sendall((command + "\n").encode())
            reply = sock.recv(4096).decode().strip()
    except OSError as exc:
        raise FirewallError(f"нет связи с ipmi-fw-helper ({path}): {exc}") from exc
    if not reply.startswith("OK"):
        raise FirewallError(reply or "пустой ответ от ipmi-fw-helper")
    return reply


def open_window(address: str, seconds: int = None) -> None:
    if not enabled():
        log.debug("интеграция с файрволом выключена, окно для %s не открываю", address)
        return
    seconds = seconds or current_app.config["FW_WINDOW_SECONDS"]
    _send(f"ALLOW {address} {int(seconds)}")


def close_window(address: str) -> None:
    if not enabled():
        return
    _send(f"DENY {address}")


def available() -> bool:
    """Отвечает ли демон. Выключенная интеграция — это не «недоступен»,
    поэтому проверять состояние имеет смысл только при включённой."""
    if not enabled():
        return False
    try:
        _send("PING")
        return True
    except FirewallError:
        return False


def status() -> tuple[str, str]:
    """Состояние для интерфейса: (код, человеческое описание)."""
    if not enabled():
        return "off", (
            "Управление файрволом выключено настройкой. Доступ к шаге с образами "
            "ограничивается чем-то другим — убедись, что это так."
        )
    if available():
        return "ok", "Окно доступа открывается на время монтирования."
    return "broken", (
        "Демон ipmi-fw-helper не отвечает. Монтирование не начнётся: открыть "
        "доступ BMC к шаре некому."
    )
