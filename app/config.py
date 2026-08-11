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
    # Короткий TTL с автопродлением, пока BMC подтверждает монтирование.
    # Длинный фиксированный не нужен: продление делает фоновая задача, а
    # короткое окно означает, что падение сервиса закроет порт за полчаса, а
    # не оставит открытым на смену.
    FW_WINDOW_SECONDS = _int("FW_WINDOW_SECONDS", 1800)
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

    # Карточка сервера опрашивает BMC синхронно при каждом открытии, а нужна
    # она чаще всего тогда, когда сервер лежит. Поэтому ждать долго нельзя:
    # худший случай здесь — около 25 секунд, а не полторы минуты.
    BMC_HTTP_TIMEOUT = _int("BMC_HTTP_TIMEOUT", 15)
    BMC_IPMI_INTERVAL = _int("BMC_IPMI_INTERVAL", 3)
    BMC_IPMI_RETRIES = _int("BMC_IPMI_RETRIES", 2)

    # Записи о попытках входа нужны только на время окна блокировки; хранить
    # их вечно незачем.
    LOGIN_ATTEMPT_RETENTION_DAYS = _int("LOGIN_ATTEMPT_RETENTION_DAYS", 7)

    # Прокси консолей. CONSOLE_HOST — то самое имя, по которому открывается
    # панель: браузер отдаёт сессию BMC только на тот же хост, отличаться
    # должен лишь порт. Порт закрепляется за сервером в его карточке.
    CONSOLE_HOST = os.environ.get("CONSOLE_HOST", "")
    CONSOLE_PORT_BASE = _int("CONSOLE_PORT_BASE", 7001)
    CONSOLE_SSL_CERT = os.environ.get("CONSOLE_SSL_CERT", "")
    CONSOLE_SSL_KEY = os.environ.get("CONSOLE_SSL_KEY", "")

    MAX_CONTENT_LENGTH = None  # загрузка идёт чанками, лимит не нужен
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 12
