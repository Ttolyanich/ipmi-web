"""Драйвер Supermicro: виртуальные медиа через вебморду, питание через IPMI.

API вебморды публично не документирован, снят с живой машины X11DPL-i FW 1.76 —
подробности и ограничения в docs/bmc-api.md. Ключевое, что стоило половины
отладки: `op.cgi` требует заголовок CSRF_TOKEN, а без него отвечает 403 с
пустым телом, по которому причину не угадать.
"""
import logging
import os
import re
import subprocess
import xml.etree.ElementTree as ET

import requests
import urllib3

from .base import BOOT_TARGETS, POWER_ACTIONS, BmcDriver, DriverError, MediaSlot

# На BMC стоит самоподписанный сертификат, у многих просроченный. Проверку
# отключаем осознанно: включить её можно только после замены сертификата на
# всех узлах, и тогда это станет отдельным решением.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger(__name__)

_CSRF_RE = re.compile(r'SmcCsrfInsert\s*\(\s*"CSRF_TOKEN"\s*,\s*"([^"]+)"')

# Статусы слотов из ответа vm_uiso_status: 4 — образ смонтирован,
# 255 — слот пуст ("No disk emulation set" в вебморде).
_STATUS_MOUNTED = "4"

_POWER_IPMI = {
    "on": "on",
    "off": "off",
    "reset": "reset",
    "soft": "soft",
}


class SupermicroDriver(BmcDriver):
    vendor = "supermicro"

    def __init__(
        self,
        address: str,
        username: str,
        password: str,
        timeout: int = 15,
        cipher_suite: str = "3",
        ipmi_interval: int = 3,
        ipmi_retries: int = 2,
    ):
        super().__init__(address, username, password)
        # Таймауты держим короткими сознательно. Карточка сервера опрашивает
        # BMC синхронно при каждом открытии, а нужна она чаще всего тогда,
        # когда сервер уже лежит. Полторы минуты ожидания в этот момент —
        # худшее из возможных поведений.
        self.timeout = timeout
        self.ipmi_interval = ipmi_interval
        self.ipmi_retries = ipmi_retries
        # Набор шифров указывается явно. ipmitool по умолчанию пробует свой
        # (обычно 17), и если на BMC разрешён только третий — а это как раз
        # рекомендуемая настройка, см. docs/bmc-hardening.md, — сессия падает
        # с «invalid role», по которому причину не угадать.
        self.cipher_suite = cipher_suite
        self._session = None
        self._token = None

    # --- сессия ---

    @property
    def _base(self) -> str:
        return f"https://{self.address}"

    def _login(self) -> None:
        session = requests.Session()
        session.verify = False
        try:
            session.post(
                f"{self._base}/cgi/login.cgi",
                data={"name": self.username, "pwd": self.password},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise DriverError(f"BMC {self.address} недоступен: {exc}") from exc

        if not session.cookies.get("SID"):
            raise DriverError(f"BMC {self.address}: вход не выполнен, проверь учётные данные")

        # Токен вшит в страницу topmenu вызовом SmcCsrfInsert.
        page = session.get(
            f"{self._base}/cgi/url_redirect.cgi",
            params={"url_name": "topmenu"},
            timeout=self.timeout,
        )
        match = _CSRF_RE.search(page.text)
        if not match:
            raise DriverError(
                f"BMC {self.address}: не найден CSRF_TOKEN — вероятно, другая версия прошивки"
            )

        self._session = session
        self._token = match.group(1)

    def _op(self, retry: bool = True, **fields) -> str:
        if self._session is None:
            self._login()

        headers = {
            "CSRF_TOKEN": self._token,
            "X-Requested-With": "XMLHttpRequest",
        }
        try:
            response = self._session.post(
                f"{self._base}/cgi/op.cgi",
                data=fields,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise DriverError(f"BMC {self.address}: запрос не прошёл: {exc}") from exc

        # Сессия и токен живут ограниченное время; при их истечении прошивка
        # отвечает 403 с пустым телом. Один прозрачный перелогин.
        if response.status_code == 403 and retry:
            log.info("BMC %s: сессия истекла, повторный вход", self.address)
            self._session = None
            return self._op(retry=False, **fields)

        if response.status_code != 200:
            raise DriverError(
                f"BMC {self.address}: {fields.get('op')} вернул HTTP {response.status_code}"
            )
        return response.text

    # --- консоль ---

    CONSOLE_PATH = "/cgi/url_redirect.cgi?url_name=man_ikvm_html5_bootstrap"

    def console_session(self) -> tuple[str, str, str]:
        if self._session is None:
            self._login()
        return "SID", self._session.cookies.get("SID"), self.CONSOLE_PATH

    # --- виртуальные медиа ---

    def media_path(self, share: str, filename: str) -> str:
        # Прошивка ждёт обратные слэши и путь, начинающийся с имени шары.
        return f"\\{share}\\{filename}"

    def insert_media(self, share_host: str, path: str, user: str, password: str) -> None:
        result = self._op(
            op="config_iso",
            host=share_host,
            path=path,
            user=user,
            pwd=password,
        ).strip()
        if result.lower() != "ok":
            raise DriverError(f"BMC {self.address}: настройки образа не приняты ({result!r})")
        self._op(op="mount_iso")

    def eject_media(self) -> None:
        self._op(op="umount_iso")

    def media_status(self) -> list[MediaSlot]:
        raw = self._op(op="vm_uiso_status")
        try:
            root = ET.fromstring(raw.strip())
        except ET.ParseError as exc:
            raise DriverError(f"BMC {self.address}: не разобрать ответ о статусе: {exc}") from exc

        slots = []
        for device in root.findall("DEVICE"):
            status = device.get("STATUS", "")
            slots.append(
                MediaSlot(
                    index=int(device.get("ID", "0")),
                    mounted=status == _STATUS_MOUNTED,
                    raw_status=status,
                )
            )
        return slots

    # --- питание и загрузка ---
    # Идут через IPMI, а не через вебморду: протокол стабильный, лицензии не
    # требует и одинаков у всех вендоров.

    def _ipmitool(self, *args: str) -> str:
        command = [
            "ipmitool", "-I", "lanplus",
            "-H", self.address,
            "-U", self.username,
            "-E",  # пароль берётся из IPMI_PASSWORD, а не из аргументов
            "-C", self.cipher_suite,
            "-L", "ADMINISTRATOR",
            # -N/-R задают тайм-аут и число повторов на уровне самого RMCP+.
            # Без них ipmitool молча ждёт своих умолчаний, и внешний тайм-аут
            # срабатывает уже как аварийный.
            "-N", str(self.ipmi_interval),
            "-R", str(self.ipmi_retries),
            *args,
        ]
        # Через -P пароль виден в списке процессов любому, кто может читать
        # /proc. Переменная окружения видна только самому процессу и root.
        environment = {**os.environ, "IPMI_PASSWORD": self.password}
        hard_limit = self.ipmi_interval * (self.ipmi_retries + 1) + 10
        result = subprocess.run(
            command, capture_output=True, timeout=hard_limit, check=False, env=environment
        )
        if result.returncode != 0:
            message = result.stderr.decode(errors="replace").strip() or "неизвестная ошибка"
            if "invalid role" in message or "RMCP+" in message:
                message += (
                    f" (набор шифров {self.cipher_suite} не принят; "
                    "проверь 'Cipher Suite Priv Max' в выводе ipmitool lan print "
                    "и задай IPMI_CIPHER_SUITE)"
                )
            raise DriverError(f"BMC {self.address}: ipmitool {' '.join(args)}: {message}")
        return result.stdout.decode(errors="replace").strip()

    def set_boot_override(self, target: str = "cdrom") -> None:
        if target not in BOOT_TARGETS:
            raise DriverError(f"неизвестное устройство загрузки: {target}")
        # Без options=persistent: постоянный флаг заставит сервер грузиться с
        # образа после установки, то есть бесконечный цикл. BMC сбрасывает
        # одноразовый флаг сам после первой загрузки.
        self._ipmitool("chassis", "bootdev", target)

    def power_action(self, action: str) -> None:
        if action not in POWER_ACTIONS:
            raise DriverError(f"неизвестное действие с питанием: {action}")
        self._ipmitool("chassis", "power", _POWER_IPMI[action])

    def power_state(self) -> str:
        out = self._ipmitool("chassis", "power", "status")
        return "on" if out.endswith("on") else "off"
