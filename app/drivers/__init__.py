"""Реестр драйверов.

Пока реализация одна. Добавление вендора — новый модуль и строка здесь;
переделывать ядро не требуется, интерфейс описан в base.py в терминах Redfish.
"""
from .base import BmcDriver, DriverError, MediaSlot
from .supermicro import SupermicroDriver

_REGISTRY = {
    SupermicroDriver.vendor: SupermicroDriver,
}

VENDORS = tuple(_REGISTRY)


def get_driver(server) -> BmcDriver:
    from ..crypto import decrypt

    cls = _REGISTRY.get(server.vendor)
    if cls is None:
        raise DriverError(
            f"для вендора {server.vendor!r} драйвера нет — поддерживаются: {', '.join(VENDORS)}"
        )
    return cls(server.address, server.bmc_user, decrypt(server.bmc_password_enc))


__all__ = ["BmcDriver", "DriverError", "MediaSlot", "VENDORS", "get_driver"]
