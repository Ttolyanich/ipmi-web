"""Аудит-журнал.

Пишется на каждое действие, меняющее состояние железа. Без него при разборе
инцидента доказать ничего нельзя.
"""
from flask import has_request_context

from .models import AuditEntry, db
from .security import client_ip, current_user


def log(action: str, server_name: str = None, detail: str = None, username: str = None) -> None:
    if username is None and has_request_context():
        user = current_user()
        username = user.username if user else None
    entry = AuditEntry(
        username=username or "system",
        ip=client_ip() if has_request_context() else "-",
        action=action,
        server_name=server_name,
        detail=detail,
    )
    db.session.add(entry)
    db.session.commit()
