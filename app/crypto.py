"""Шифрование паролей BMC.

Пароли от BMC — это доступ ниже уровня ОС, поэтому в базе они лежат только
зашифрованными. Ключ живёт в окружении и в репозиторий не попадает.
"""
import secrets
import string

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

# Прошивка Supermicro не принимает спецсимволы в пароле к шаре — проверено
# на живой машине. Поэтому алфавит одноразовых паролей только буквенно-цифровой.
_SMB_ALPHABET = string.ascii_letters + string.digits


def _fernet() -> Fernet:
    key = current_app.config.get("FERNET_KEY")
    if not key:
        raise RuntimeError("FERNET_KEY не задан — пароли BMC хранить негде")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(value: str) -> bytes:
    return _fernet().encrypt((value or "").encode())


def decrypt(token: bytes) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token).decode()
    except InvalidToken:
        raise RuntimeError(
            "Пароль не расшифровывается: FERNET_KEY изменился или база от другой установки"
        )


def one_time_password(length: int = 20) -> str:
    return "".join(secrets.choice(_SMB_ALPHABET) for _ in range(length))


def one_time_username(prefix: str = "vm") -> str:
    # Имя тоже одноразовое: перехваченный NTLMv1-хэш бесполезен, если учётной
    # записи уже не существует.
    return prefix + secrets.token_hex(5)
