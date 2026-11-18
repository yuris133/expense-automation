#!/usr/bin/env bash
# Ежедневный бэкап данных бота (том не резервирование Oracle — держим локальные копии: 14 дней).
set -euo pipefail
STAMP=$(date +%F)
DEST=/opt/backups/$STAMP
mkdir -p "$DEST"
rsync -a /opt/receiptsbot/data/ "$DEST/data/"
rsync -a /opt/receiptsbot/reports/ "$DEST/reports/"
find /opt/backups -maxdepth 1 -type d -name '20*' -mtime +14 -exec rm -rf {} + 2>/dev/null || true
# «синяя кнопка»: последняя копия всегда под рукой
rsync -a --delete /opt/backups/$STAMP/ /opt/backups/latest/
echo "[$(date '+%F %T')] backup -> $DEST"