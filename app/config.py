"""Конфигурация из окружения. Значения по умолчанию рассчитаны на контейнер."""
import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on", "да")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "")
    FERNET_KEY = os.environ.get("FERNET_KEY", "")

    DATA_DIR = os.environ.get("DATA_DIR", "/data")
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(DATA_DIR, "ipmi-web.db")
    # timeout — это busy_timeout SQLite. Без него фоновая задача скачивания и
    # обычный запрос, пишущие одновременно, дают «database is locked».
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "connect_args": {"timeout": 30},
    }

    # Библиотека образов. Тот же каталог отдаётся по SMB как шара ISO_SHARE.
    ISO_DIR = os.environ.get("ISO_DIR", "/srv/iso")
    ISO_SHARE = os.environ.get("ISO_SHARE", "iso")

    # Адрес, по которому BMC видит наш SMB-сервер. Порт указать нельзя —
    # прошивка не принимает двоеточие в поле Share host.
    SHARE_HOST = os.environ.get("SHARE_HOST", "")

    # Жёсткий лимит прошивки Supermicro, заявленный на странице CD-ROM Image.
    # 4.7 ГБ в десятичном счислении, как пишут на болванках.
    MAX_IMAGE_BYTES = _int("MAX_IMAGE_BYTES", 4_700_000_000)

    # Окно доступа к 445 порту. Держится, пока BMC подтверждает монтирование,
    # и продлевается фоновой задачей. Короткий TTL безопаснее длинного: если
    # сервис умрёт, ядро закроет порт само.
    # Интеграция с файрволом хоста. По умолчанию включена: без неё порт 445
    # доступен всем, кто может до него достучаться, и защита сводится к
    # проверке внутри Samba — то есть уже после того, как разборщик SMB1
    # принял пакет. Выключать осмысленно только там, где шара живёт в
    # доверенной сети и доступ ограничен другими средствами.
    FIREWALL_ENABLED = _bool("FIREWALL_ENABLED", True)
    FW_SOCKET = os.environ.get("FW_SOCKET", "/run/ipmi-fw/helper.sock")
    FW_WINDOW_SECONDS = _int("FW_WINDOW_SECONDS", 28800)
    MOUNT_POLL_SECONDS = _int("MOUNT_POLL_SECONDS", 300)

    # Группа, членство в которой открывает доступ к шаре. Одноразовые
    # пользователи заводятся в неё и удаляются при размонтировании.
    SMB_GROUP = os.environ.get("SMB_GROUP", "ikvm")

    # Первый администратор создаётся при первом старте. Хардкода нет:
    # без этих переменных вход будет невозможен, и это осознанно.
    BOOTSTRAP_ADMIN = os.environ.get("BOOTSTRAP_ADMIN", "")
    BOOTSTRAP_PASSWORD = os.environ.get("BOOTSTRAP_PASSWORD", "")

    LOGIN_MAX_ATTEMPTS = _int("LOGIN_MAX_ATTEMPTS", 5)
    LOGIN_WINDOW_SECONDS = _int("LOGIN_WINDOW_SECONDS", 300)
    MIN_PASSWORD_LENGTH = _int("MIN_PASSWORD_LENGTH", 8)

    # Набор шифров RMCP+ для ipmitool. Указывается явно: умолчание ipmitool
    # (17) не работает на BMC, где разрешён только третий — а это как раз
    # рекомендуемая настройка. Симптом — «invalid role».
    IPMI_CIPHER_SUITE = os.environ.get("IPMI_CIPHER_SUITE", "3")

    MAX_CONTENT_LENGTH = None  # загрузка идёт чанками, лимит не нужен
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 12
