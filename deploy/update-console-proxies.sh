#!/bin/bash
# Пересобрать конфигурацию прокси консолей из инвентаря сервиса.
#
# Запускать на хосте после добавления или удаления серверов:
#     bash deploy/update-console-proxies.sh
#
# Конфигурацию печатает приложение (у него инвентарь и пароли), а пишет её и
# перезагружает nginx этот скрипт (у него есть права). Приложение к nginx не
# прикасается.
#
# Скрипт идемпотентен: порт за сервером закрепляется в его карточке, поэтому
# повторный запуск даёт тот же результат и ссылки не разъезжаются.
set -euo pipefail

CONTAINER="${CONTAINER:-ipmi-web}"
TARGET="${TARGET:-/etc/nginx/conf.d/ipmi-console.conf}"
MAP="${MAP:-/etc/nginx/conf.d/upgrade-map.conf}"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    echo "контейнер $CONTAINER не запущен" >&2
    exit 1
fi

# Карта апгрейда нужна блокам консолей. Безусловный `Connection: upgrade`
# ломает обычные запросы, поэтому именно карта.
if [ ! -f "$MAP" ]; then
    cat > "$MAP" <<'EOF'
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
EOF
    echo "создана карта $MAP"
fi

generated=$(docker exec -w /app "$CONTAINER" python -m app.console_nginx)
if [ -z "$generated" ]; then
    echo "приложение вернуло пустую конфигурацию, ничего не меняю" >&2
    exit 1
fi

if [ -f "$TARGET" ] && [ "$generated" = "$(cat "$TARGET")" ]; then
    echo "изменений нет"
    exit 0
fi

backup=""
if [ -f "$TARGET" ]; then
    backup="${TARGET}.bak"
    cp -a "$TARGET" "$backup"
fi

printf '%s' "$generated" > "$TARGET"

# Блоки, разложенные по отдельным файлам до появления генератора, теперь
# дублировали бы порты.
for stale in /etc/nginx/conf.d/ipmi-console-*.conf; do
    [ -e "$stale" ] || continue
    mv "$stale" "${stale}.disabled"
    echo "отключён старый файл $stale"
done

if ! nginx -t; then
    echo "конфигурация не прошла проверку, откатываюсь" >&2
    if [ -n "$backup" ]; then
        mv "$backup" "$TARGET"
    else
        rm -f "$TARGET"
    fi
    for restored in /etc/nginx/conf.d/ipmi-console-*.conf.disabled; do
        [ -e "$restored" ] || continue
        mv "$restored" "${restored%.disabled}"
    done
    exit 1
fi

systemctl reload nginx
echo "готово, прокси консолей обновлены:"
grep -E '^# |listen ' "$TARGET" | sed 's/^/  /'
