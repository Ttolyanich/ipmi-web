"""Интерфейс драйвера BMC.

Названия операций намеренно взяты из Redfish (`InsertMedia`, `EjectMedia`,
`Boot` override, `Reset`), хотя реализация сейчас одна и она — обход вебморды
Supermicro. Причина в DECISIONS.md: если ядро примет форму
Supermicro-костыля, Redfish потом будет прикручен сбоку и код останется кривым
навсегда. При такой форме добавление вендора — новый файл, а не переделка.
"""
from dataclasses import dataclass


class DriverError(RuntimeError):
    """Ошибка, которую можно показать пользователю как есть."""


@dataclass
class MediaSlot:
    index: int
    mounted: bool
    raw_status: str


POWER_ACTIONS = ("on", "off", "reset", "soft")
BOOT_TARGETS = ("cdrom", "pxe", "disk", "bios", "none")


class BmcDriver:
    vendor = "generic"

    def __init__(self, address: str, username: str, password: str):
        self.address = address
        self.username = username
        self.password = password

    # --- виртуальные медиа ---
    def insert_media(self, share_host: str, path: str, user: str, password: str) -> None:
        raise NotImplementedError

    def eject_media(self) -> None:
        raise NotImplementedError

    def media_status(self) -> list[MediaSlot]:
        raise NotImplementedError

    def media_mounted(self) -> bool:
        return any(slot.mounted for slot in self.media_status())

    # --- питание и загрузка ---
    def set_boot_override(self, target: str = "cdrom") -> None:
        raise NotImplementedError

    def power_action(self, action: str) -> None:
        raise NotImplementedError

    def power_state(self) -> str:
        raise NotImplementedError

    # --- как драйвер хочет видеть путь к образу ---
    def media_path(self, share: str, filename: str) -> str:
        return f"/{share}/{filename}"
