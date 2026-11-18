#!/bin/bash
# Старт в контейнере: постоянные данные живут на томе /data, приложение — в /app.
# Папки data/receipts/reports/logs заменяются симлинками на том — ROOT-относительные пути кода не меняются.
set -e
mkdir -p /data/data /data/receipts /data/reports /data/logs
for dir in data receipts reports logs; do
    if [ ! -e "/app/$dir" ]; then
        ln -s "/data/$dir" "/app/$dir"
    fi
done
exec python -u src/telegram_bot.py