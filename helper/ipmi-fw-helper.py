#!/usr/bin/env python3
"""Хостовый демон, управляющий окном доступа к SMB-шаре.

Зачем отдельный процесс: веб-морда хранит пароли от всех BMC, и давать ей
NET_ADMIN на хосте — значит превратить её компрометацию в контроль над
файрволом узла. Демон умеет ровно две операции и ничего больше.

Протокол — текстовый, по unix-сокету, по строке на запрос:

    PING                -> OK
    ALLOW <ip> <секунд> -> OK
    DENY <ip>           -> OK

Множество лежит внутри существующей таблицы файрвола узла: своя базовая
цепочка на узле с белым списком и policy drop ничего не решает — accept
завершает обход только своей цепочки, и пакет всё равно приходит в основную,
где его выбрасывает политика. Проверено на практике.

Зависимостей нет, только стандартная библиотека и nft.
"""
import argparse
import ipaddress
import logging
import os
import socket
import socketserver
import subprocess
import sys

TABLE = "ip filter"
SET_NAME = "ikvm_allow"
CHAIN = "INPUT"
PORT = 445

log = logging.getLogger("ipmi-fw-helper")


def nft(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["nft", *args], capture_output=True, timeout=15, check=False)


def ensure_ruleset() -> None:
    """Создать множество и правило, если их ещё нет.

    Идемпотентно: повторный запуск демона не плодит дубликаты. Сам ruleset
    узла не перечитываем — если в /etc/nftables.conf стоит `flush ruleset`,
    его перечитывание снесёт цепочки Docker.
    """
    result = nft("list", "set", *TABLE.split(), SET_NAME)
    if result.returncode != 0:
        log.info("создаю множество %s", SET_NAME)
        nft("add", "set", *TABLE.split(), SET_NAME,
            "{ type ipv4_addr; flags timeout; }")

    chain = nft("list", "chain", *TABLE.split(), CHAIN)
    if f"@{SET_NAME}" not in chain.stdout.decode(errors="replace"):
        log.info("добавляю правило для порта %s в цепочку %s", PORT, CHAIN)
        nft("insert", "rule", *TABLE.split(), CHAIN,
            "tcp", "dport", str(PORT), "ip", "saddr", f"@{SET_NAME}", "accept")


def allow(address: str, seconds: int) -> None:
    ipaddress.IPv4Address(address)
    seconds = max(60, min(int(seconds), 24 * 3600))
    # Повторный ALLOW обновляет таймаут — это и есть продление окна.
    nft("delete", "element", *TABLE.split(), SET_NAME, "{ %s }" % address)
    result = nft("add", "element", *TABLE.split(), SET_NAME,
                 "{ %s timeout %ds }" % (address, seconds))
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace").strip())
    log.info("окно открыто для %s на %d с", address, seconds)


def deny(address: str) -> None:
    ipaddress.IPv4Address(address)
    nft("delete", "element", *TABLE.split(), SET_NAME, "{ %s }" % address)
    # Уже установленная сессия переживёт удаление элемента, поэтому добиваем
    # её через conntrack. Отсутствие утилиты не считаем ошибкой.
    subprocess.run(
        ["conntrack", "-D", "-p", "tcp", "--dport", str(PORT), "-s", address],
        capture_output=True, timeout=15, check=False,
    )
    log.info("окно закрыто для %s", address)


class Handler(socketserver.StreamRequestHandler):
    timeout = 15

    def handle(self) -> None:
        try:
            line = self.rfile.readline().decode(errors="replace").strip()
        except OSError:
            return

        parts = line.split()
        try:
            if not parts:
                raise ValueError("пустая команда")
            command = parts[0].upper()

            if command == "PING":
                pass
            elif command == "ALLOW" and len(parts) == 3:
                allow(parts[1], int(parts[2]))
            elif command == "DENY" and len(parts) == 2:
                deny(parts[1])
            else:
                raise ValueError(f"не понимаю команду {line!r}")

            self.wfile.write(b"OK\n")
        except Exception as exc:  # noqa: BLE001 — ответ уходит клиенту как есть
            log.warning("ошибка обработки %r: %s", line, exc)
            self.wfile.write(f"ERR {exc}\n".encode())


class Server(socketserver.ThreadingUnixStreamServer):
    allow_reuse_address = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", default="/run/ipmi-fw/helper.sock")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    if os.geteuid() != 0:
        log.error("нужны права root: демон правит nftables")
        return 1

    ensure_ruleset()

    os.makedirs(os.path.dirname(args.socket), exist_ok=True)
    if os.path.exists(args.socket):
        os.remove(args.socket)

    with Server(args.socket, Handler) as server:
        # Сокет доступен только root: контейнер работает от root, посторонним
        # процессам на хосте открывать порты незачем.
        os.chmod(args.socket, 0o600)
        log.info("слушаю %s", args.socket)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
