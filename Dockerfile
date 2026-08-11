FROM python:3.12-slim

# samba    — раздача библиотеки образов, BMC забирает ISO сам
# smbclient — самопроверка шары изнутри контейнера
# ipmitool  — питание и однократный выбор загрузочного устройства
# passwd    — useradd/groupadd для одноразовых учётных записей
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        samba smbclient ipmitool passwd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config/smb.conf /etc/samba/smb.conf
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY app ./app
COPY wsgi.py .

RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 5006
CMD ["/usr/local/bin/docker-entrypoint.sh"]
