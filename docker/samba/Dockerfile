# Раздача библиотеки ISO по SMB для Virtual Media → CD-ROM Image.
#
# Старые BMC умеют только SMB1 с NTLMv1, поэтому образ намеренно включает
# протокол-труп. Компенсируется тем, что доступ ограничен адресом конкретного
# BMC (hosts allow в smb.conf плюс окно в nftables), шара только на чтение,
# а учётная запись создаётся одноразовой на время монтирования.
FROM debian:12-slim

# smbclient нужен для самопроверки шары изнутри контейнера: пакет samba даёт
# только серверную часть.
RUN apt-get update \
    && apt-get install -y --no-install-recommends samba smbclient \
    && rm -rf /var/lib/apt/lists/*

COPY smb.conf /etc/samba/smb.conf

# SMB_USER / SMB_PASS передаются при запуске; учётка создаётся на старте,
# чтобы пароль не оседал в слоях образа.
CMD ["sh", "-c", "\
    useradd -M -s /usr/sbin/nologin \"$SMB_USER\" 2>/dev/null; \
    printf '%s\\n%s\\n' \"$SMB_PASS\" \"$SMB_PASS\" | smbpasswd -s -a \"$SMB_USER\"; \
    exec smbd --foreground --no-process-group --debug-stdout"]
