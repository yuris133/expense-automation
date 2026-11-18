#!/usr/bin/env bash
# Установка чек-бота на Ubuntu 24.04 (ARM A1 Always Free) — запускать от root.
# Ожидает: проект уже скопирован в /opt/receiptsbot (см. README, «Хостинг на Oracle»).
set -euo pipefail

echo "=== 1/6 Пакеты системы ==="
apt-get update
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng tesseract-ocr-osd \
    rsync

echo "=== 2/6 Пользователь bot ==="
id -u bot >/dev/null 2>&1 || useradd -r -m -d /opt/receiptsbot -s /usr/sbin/nologin bot
chown -R bot:bot /opt/receiptsbot

echo "=== 3/6 Виртуальное окружение и зависимости ==="
sudo -u bot python3 -m venv /opt/receiptsbot/venv
sudo -u bot /opt/receiptsbot/venv/bin/pip install --upgrade pip
sudo -u bot /opt/receiptsbot/venv/bin/pip install -r /opt/receiptsbot/deploy/requirements.txt

echo "=== 4/6 Секреты из локального конфига -> /etc/receiptsbot.env ==="
# Токены храним вне проекта (в образе их нет). Если файла ещё нет — создаём заглушку.
mkdir -p /etc
if [ ! -f /etc/receiptsbot.env ]; then
    cat > /etc/receiptsbot.env <<'ENV'
TELEGRAM_BOT_TOKEN=
PROVERKACHEKA_TOKEN=
ENV
    echo "Заполните /etc/receiptsbot.env токенами и повторите systemctl restart receipts-bot"
fi
chmod 600 /etc/receiptsbot.env

echo "=== 5/6 Сервис и анти-реклайн ==="
cp /opt/receiptsbot/deploy/receipts-bot.service /etc/systemd/system/
# дополнение юнита: секреты из /etc/receiptsbot.env
cat >> /etc/systemd/system/receipts-bot.service <<EOF
EnvironmentFile=/etc/receiptsbot.env
EOF
systemctl daemon-reload
systemctl enable --now receipts-bot

cp /opt/receiptsbot/deploy/anti_reclaim.sh /usr/local/bin/anti_reclaim.sh && chmod +x /usr/local/bin/anti_reclaim.sh
cp /opt/receiptsbot/deploy/receipts_backup.sh /usr/local/bin/ && chmod +x /usr/local/bin/receipts_backup.sh
cat > /etc/cron.d/receiptsbot <<'CRON'
# анти-реклайн: держим CPU-пик выше порога Oracle (см. README)
*/2 * * * * root /usr/local/bin/anti_reclaim.sh
# бэкап данных: ежедневно 04:30 МСК, ротация 14 дней
30 4 * * * root /usr/local/bin/receipts_backup.sh
CRON

echo "=== 6/6 Статус ==="
systemctl --no-pager status receipts-bot | head -15
echo "Готово. Логи: journalctl -u receipts-bot -f"