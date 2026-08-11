"""Генератор конфигурации прокси консолей.

Печатает в стандартный вывод по одному server-блоку на сервер. Ничего не
пишет на диск и не трогает nginx: этим занимается скрипт на хосте
(`deploy/update-console-proxies.sh`). Граница та же, что и с файрволом —
приложение хранит пароли от всех BMC, и права на конфигурацию веб-сервера ему
ни к чему.

Запуск:
    docker exec -w /app ipmi-web python -m app.console_nginx

Порт каждому серверу назначается один раз и запоминается в его карточке
(поле «Прокси консоли»), поэтому повторный запуск даёт тот же результат и
существующие ссылки не разъезжаются.
"""
import sys
from urllib.parse import urlparse

from flask import current_app

from .models import Server, db

TEMPLATE = """\
# {name} — {address}
server {{
    listen {port} ssl;
    http2 on;
    server_name {host};

    ssl_certificate     {cert};
    ssl_certificate_key {key};
    ssl_protocols TLSv1.2 TLSv1.3;

    access_log /var/log/nginx/ipmi-console-{port}.access.log;
    error_log  /var/log/nginx/ipmi-console-{port}.error.log;

    location / {{
        proxy_pass https://{address};

        # На BMC самоподписанный, часто просроченный сертификат.
        proxy_ssl_verify off;
        proxy_ssl_server_name on;

        proxy_set_header Host              $proxy_host;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Видео и клавиатура идут по WebSocket.
        proxy_http_version 1.1;
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection $connection_upgrade;

        # Прошивка запрещает встраивать себя во фрейм; раз страницу отдаём мы,
        # запрет снимаем.
        proxy_hide_header X-Frame-Options;
        proxy_hide_header Content-Security-Policy;

        # Консоль держит соединение всё время работы, буферизация интерактиву
        # только вредит.
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_buffering off;
        client_max_body_size 0;
    }}
}}
"""


def _port_of(server: Server, host: str) -> int | None:
    """Порт из карточки, если он ведёт на наш же хост.

    Адрес, указанный вручную и ведущий куда-то ещё, считаем чужим и не трогаем:
    администратор мог настроить прокси сам.
    """
    if not server.console_url:
        return None
    parsed = urlparse(server.console_url)
    if parsed.hostname != host:
        return None
    return parsed.port


def assign_ports(host: str, base: int) -> list[tuple[Server, int]]:
    servers = Server.query.order_by(Server.id).all()
    taken = {port for port in (_port_of(s, host) for s in servers) if port}
    result = []

    for server in servers:
        if server.console_url and _port_of(server, host) is None:
            continue  # чужой прокси, настроен вручную

        port = _port_of(server, host)
        if port is None:
            port = base
            while port in taken:
                port += 1
            taken.add(port)
            server.console_url = f"https://{host}:{port}"
            db.session.add(server)

        result.append((server, port))

    db.session.commit()
    return result


def render() -> str:
    host = current_app.config["CONSOLE_HOST"]
    if not host:
        raise RuntimeError(
            "CONSOLE_HOST не задан. Это имя, по которому открывается панель: "
            "браузер отдаёт сессию BMC только на тот же хост, отличаться должен "
            "лишь порт."
        )

    cert = current_app.config["CONSOLE_SSL_CERT"] or f"/etc/letsencrypt/live/{host}/fullchain.pem"
    key = current_app.config["CONSOLE_SSL_KEY"] or f"/etc/letsencrypt/live/{host}/privkey.pem"
    pairs = assign_ports(host, current_app.config["CONSOLE_PORT_BASE"])

    header = (
        "# Сгенерировано app/console_nginx.py — правки будут затёрты.\n"
        "# Требуется карта $connection_upgrade (см. deploy/nginx-ipmi-web.conf).\n"
    )
    if not pairs:
        return header + "# Серверов в инвентаре нет.\n"

    blocks = [
        TEMPLATE.format(
            name=server.name, address=server.address, port=port,
            host=host, cert=cert, key=key,
        )
        for server, port in pairs
    ]
    return header + "\n".join(blocks)


def main() -> int:
    import os

    # Разовой команде планировщик не нужен: он бы поднял фоновые задачи на
    # доли секунды и в худшем случае успел дёрнуть опрос BMC.
    os.environ["ENABLE_SCHEDULER"] = "0"

    from . import create_app

    app = create_app()
    with app.app_context():
        try:
            sys.stdout.write(render())
        except RuntimeError as exc:
            print(f"ошибка: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
