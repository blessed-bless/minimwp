#!/usr/bin/env bash
# Разворачивает мини-CRM на сервере. Идемпотентен: повторный запуск обновляет код.
# Ничего не трогает в чужих конфигах — Caddy настраивается отдельно, см. caddy_site.sh.
set -euo pipefail

APP_DIR=/opt/minicrm
ENV_FILE=/etc/minicrm.env
REPO=https://github.com/blessed-bless/minimwp.git
PORT=8012

echo "--- пакеты ---"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3-venv python3-pip >/dev/null

echo "--- пользователь службы ---"
id -u minicrm >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin minicrm

echo "--- код ---"
# Каталог принадлежит minicrm, а git работает от root: без этого он ругается
# на dubious ownership и обновление кода падает.
git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" fetch --quiet origin
    git -C "$APP_DIR" reset --hard --quiet origin/main
else
    git clone --quiet "$REPO" "$APP_DIR"
fi

echo "--- зависимости ---"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

echo "--- настройки ---"
# Секреты живут вне репозитория, чтобы git reset --hard их не затирал.
if [ ! -f "$ENV_FILE" ]; then
    CRM_PASSWORD=$(head -c 32 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 16)
    cat > "$ENV_FILE" <<EOF
BOT_TOKEN=
TG_API_ID=
TG_API_HASH=
CRM_USER=admin
CRM_PASSWORD=$CRM_PASSWORD
DATABASE_URL=sqlite+aiosqlite:////var/lib/minicrm/crm.db
EOF
    echo "создан $ENV_FILE, пароль CRM сгенерирован"
else
    echo "$ENV_FILE уже есть, не трогаю"
fi
chmod 600 "$ENV_FILE"
chown root:minicrm "$ENV_FILE"
chmod 640 "$ENV_FILE"

install -d -o minicrm -g minicrm /var/lib/minicrm
chown -R minicrm:minicrm "$APP_DIR"

echo "--- служба ---"
install -m 644 "$APP_DIR/deploy/minicrm.service" /etc/systemd/system/minicrm.service
systemctl daemon-reload
systemctl enable --quiet minicrm
systemctl restart minicrm
sleep 4

echo "--- проверка ---"
systemctl is-active minicrm
curl -fsS "http://127.0.0.1:$PORT/healthz" && echo
echo "Готово. Токен бота впишите в $ENV_FILE и выполните: systemctl restart minicrm"
