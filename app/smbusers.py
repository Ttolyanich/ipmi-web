"""Одноразовые учётные записи для SMB-шары.

Старые BMC умеют только SMB1 с NTLMv1, чей хэш снимается пассивно и ломается
офлайн. Поэтому учётка живёт ровно столько, сколько идёт монтирование: снятый
хэш не стоит ничего, если пользователя уже не существует.

Доступ к шаре даёт членство в группе (`valid users = @<группа>` в smb.conf),
поэтому конфигурацию Samba перечитывать не нужно — а значит, заведение
пользователя не может оборвать уже идущую установку.
"""
import logging
import subprocess

from flask import current_app

log = logging.getLogger(__name__)


class SmbUserError(RuntimeError):
    pass


def _run(args, stdin: str = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        input=stdin.encode() if stdin else None,
        capture_output=True,
        timeout=30,
        check=False,
    )


def ensure_group() -> None:
    group = current_app.config["SMB_GROUP"]
    if _run(["getent", "group", group]).returncode != 0:
        _run(["groupadd", group])


def create(username: str, password: str) -> None:
    ensure_group()
    group = current_app.config["SMB_GROUP"]

    result = _run(["useradd", "-M", "-N", "-s", "/usr/sbin/nologin", "-g", group, username])
    if result.returncode != 0:
        raise SmbUserError(f"useradd: {result.stderr.decode(errors='replace').strip()}")

    result = _run(["smbpasswd", "-s", "-a", username], stdin=f"{password}\n{password}\n")
    if result.returncode != 0:
        _run(["userdel", username])
        raise SmbUserError(f"smbpasswd: {result.stderr.decode(errors='replace').strip()}")


def drop(username: str) -> None:
    """Удаление не должно ронять размонтирование: если учётки уже нет, это не
    ошибка, а нормальный исход повторного вызова."""
    if not username:
        return
    _run(["smbpasswd", "-x", username])
    _run(["userdel", username])
    log.info("одноразовая учётка %s удалена", username)
