#!/bin/sh
set -e

mkdir -p /data /srv/iso /var/lib/samba/private /run/samba

# Группа, членство в которой открывает доступ к шаре. Одноразовые учётки
# заводятся в неё приложением и удаляются при размонтировании.
getent group "${SMB_GROUP:-ikvm}" >/dev/null || groupadd "${SMB_GROUP:-ikvm}"

# Samba поднимается рядом с приложением в одном контейнере: приложению нужно
# заводить и удалять учётные записи на каждое монтирование, а делать это через
# границу контейнеров пришлось бы пробросом docker.sock — то есть отдать
# веб-морде управление демоном Docker. Это дороже, чем один общий контейнер.
smbd --foreground --no-process-group --debug-stdout &

# Один воркер: планировщик APScheduler работает внутри процесса, и в
# нескольких воркерах задача опроса BMC дублировалась бы.
exec gunicorn \
    --workers 1 \
    --threads 16 \
    --worker-class gthread \
    --timeout 300 \
    --bind 0.0.0.0:"${PORT:-5006}" \
    --access-logfile - \
    wsgi:app
