#!/usr/bin/env bash
# Меняет пароль входа в CRM. Пароль вводится с клавиатуры и не отображается,
# поэтому не попадает ни в историю команд, ни на экран.
set -euo pipefail

ENV_FILE=/etc/minicrm.env

read -rsp 'Новый пароль для входа в CRM: ' PASS
echo
read -rsp 'Повторите пароль: ' PASS2
echo

if [ "$PASS" != "$PASS2" ]; then
    echo 'Пароли не совпали, ничего не меняю.'
    exit 1
fi

if [ ${#PASS} -lt 8 ]; then
    echo 'Слишком короткий пароль: нужно хотя бы 8 символов.'
    exit 1
fi

case "$PASS" in
    *[\'\"\$\\]*)
        echo 'Уберите из пароля кавычки, доллар и обратный слэш — systemd их читает по-своему.'
        exit 1
        ;;
esac

TMP=$(mktemp)
grep -v '^CRM_PASSWORD=' "$ENV_FILE" > "$TMP"
printf 'CRM_PASSWORD=%s\n' "$PASS" >> "$TMP"
mv "$TMP" "$ENV_FILE"
chown root:minicrm "$ENV_FILE"
chmod 640 "$ENV_FILE"
unset PASS PASS2

systemctl restart minicrm
sleep 6
systemctl is-active minicrm
echo 'Пароль обновлён. Логин: admin'
