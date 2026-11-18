# Деплой чек-бота на Fly.io — краткая шпаргалка

Полную версию с пояснениями см. в основном README.md, раздел «Хостинг на Fly.io».

## Шаги на вашем компьютере

```powershell
winget install Flyctl.flyctl
fly auth signup                       # или fly auth login
cd "C:\Users\Windows\Documents\Карьера\Зерокодер\Claude Code\Автоматизация чеков"

# в deploy/fly.toml поставьте уникальное имя приложения (app = "receipts-bot-хвост")
fly launch --no-deploy
fly volumes create receipts_data --size 3 --region fra
fly secrets set TELEGRAM_BOT_TOKEN="токен-от-BotFather" PROVERKACHEKA_TOKEN="токен-детализации"
fly ssh sftp put data/positions.json /data/data/positions.json
fly ssh sftp put data/seen_hashes.json /data/data/seen_hashes.json
fly deploy
fly logs -f                           # ждём строку «Бот запущен. Ожидаю чеки…»
```

## После запуска

- проверьте в Telegram: `/status` у бота;
- локальную задачу планировщика удалите: `schtasks /Delete /TN "ReceiptsBot" /F`;

## Бэкап с облака

```powershell
fly ssh sftp get /data/data/positions.json data/positions.json
fly ssh sftp get /data/data/seen_hashes.json data/seen_hashes.json
```

## Обновление после правок справочника/кода

```powershell
fly deploy --local-only
```