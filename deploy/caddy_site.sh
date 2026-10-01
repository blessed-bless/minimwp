#!/usr/bin/env bash
# Добавляет в Caddy отдельный сайт для CRM. Существующие блоки не трогает:
# сначала делает резервную копию, потом дописывает свой блок в конец и
# откатывается, если caddy validate не прошёл.
set -euo pipefail

HOSTNAME_CRM=${1:?укажите домен, например crm.poiskovichek.online}
PORT=8012
CADDYFILE=/etc/caddy/Caddyfile
BACKUP="/root/Caddyfile.backup-$(date +%Y%m%d-%H%M%S)"

if grep -q "$HOSTNAME_CRM" "$CADDYFILE"; then
    echo "$HOSTNAME_CRM уже есть в Caddyfile, ничего не меняю"
    exit 0
fi

cp "$CADDYFILE" "$BACKUP"
echo "резервная копия: $BACKUP"

cat >> "$CADDYFILE" <<EOF

# Мини-CRM для заявок агентства (тестовое задание)
$HOSTNAME_CRM {
	reverse_proxy 127.0.0.1:$PORT
}
EOF

if ! caddy validate --config "$CADDYFILE" --adapter caddyfile >/dev/null 2>&1; then
    echo "caddy validate не прошёл, откатываю"
    cp "$BACKUP" "$CADDYFILE"
    caddy validate --config "$CADDYFILE" --adapter caddyfile
    exit 1
fi

systemctl reload caddy
echo "Caddy перечитал конфиг. Сертификат выпустится при первом обращении к https://$HOSTNAME_CRM"
