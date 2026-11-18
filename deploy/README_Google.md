# Чек-бот на Google Cloud e2-micro (Always Free) — детальная инструкция

Цель: бот работает 24/7 на сервере Google, бесплатно навсегда. Ваш ПК может быть выключен.
Всё из папки `deploy/` переиспользуется (`oracle_install.sh` — универсальный установщик).

---

## Этап 1. Регистрация и биллинг (~15 мин)

1. Перейдите [console.cloud.google.com](https://console.cloud.google.com), войдите Google-аккаунтом.
2. Примите условия → «Create project» → имя `receipts-bot` (уникальный ID присвоит Google).
3. Откроется требования «To use Google Cloud…, you need a billing account»:
   - Manage billing accounts → **Add billing account**;
   - тип: **Individual** (не Business); страна: **Singapore**;
   - карта Сингапура — Visa/MC — Google принимает (списка $0–1 при проверке вернут);
   - после создания привязать проект к биллинг-аккаунту (всплывёт сам).
4. **Защита от случайного счёта**: Billing → Budgets & alerts → Create budget →
   Amount: **$1** (thresholds 50%/90%/100%) — если что-то станет платным, узнаете сразу письмом.

> Free Trial $300/90 дней нам не нужен — только Always Free ресурсы.

---

## Этап 2. Создание VM (поля, где ловушки)

Console → «Compute Engine» → включить API («Enable») → VM Instances → **Create Instance**.

| Поле | Значение | Почему |
|---|---|---|
| Name | `receipts-bot` | |
| Region | **us-west1** (или us-central1 / us-east1) | Только эти три — Always Free! Другие регионы = платная VM |
| Zone | любая из региона (us-west1-a) | |
| Machine Configuration → Series | **E2** | |
| Machine type | **e2-micro** (2 shared vCPU, 1 ГБ) | Free-тип |
| Container | не использовать | |
| Boot disk → Change | OS: **Ubuntu 24.04 LTS** (minimal — ок), Type: **Standard persistent disk** (не Balanced! — платный), Size: **30 ГБ** | |
| Identity/API access | без изменений (Compute Engine default SA) | |
| Firewall | ничего не ставить (галочки можно снять) | бот наружу, входящих нет |
| Networking → Network interfaces → | **External IPv4: None** | внешние IPv4 теперь платные (~$3,65/мес)!! |
| — | Internal IPv4 auto, DNS fine | |
| Management → Delete protection | включить (не даст случайно удалить VM) | |

→ **Create**. Через минуту инстанс в списке со статусом «Running» и **без внешнего IP**.

### Включить SSH-туннелирование (IAP)

- Console: «**IAM & Admin → Identity-Aware Proxy**» → вкладка Applications →
  строка «Compute Engine instances» → включить тумблер (включит service account, подтвердите).
- Проверьте «Firewall» — политика консоли предложит создать правило для IAP:
  VPC network → Firewall → **Create Firewall Rule**:
  - Name `allow-iap-ssh`; Direction: Ingress; Action: Allow; Targets: All instances;
  - Source filter: IPv4 ranges → `35.235.240.0/20`; Protocols: TCP **22**. (IAP адреса живут в этой подсети.)

---

## Этап 3. SSH-ключ на Windows

```powershell
# один раз, если ключа ещё нет
ssh-keygen -t ed25519 -f $HOME\.ssh\gcp_bot -C "receipts-bot"
```
В `$HOME\.ssh\` появятся `gcp_bot` (приватный — держите локально) и `gcp_bot.pub` (на VM).

Вставить публичный ключ на VM (разовый способ через консоль): VM Instances → клик по имени → Edit → «SSH keys» (Metadata) → Add item → вставить `gcp_bot.pub` → Save. (После этого ключ работает для всех подключений.)

---

## Этап 4. Первое подключение и подготовка VM

Установите SDK (PowerShell):

```powershell
winget install Google.CloudSDK
gcloud init          # логин в браузере + выбрать проект receipts-bot
```

Подключение через IAP (без внешнего IP):

```powershell
gcloud compute ssh receipts-bot --zone us-west1-a --tunnel-through-iap
```

(Первая попытка спросит создать парольную фразу/push-key — согласитесь.)

Внутри VM — два служебных действия:

```bash
# swap 2 ГБ (RAM 1 ГБ мало для OCR-всплесков)
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# проверка таймзоны (бэкапы кронятся в UTC — примите как данность)
timedatectl
```

---

## Этап 5. Заливка проекта и установка

С ПК (PowerShell, из папки проекта):

```powershell
$PROJECT = "receipts-bot"; $ZONE = "us-west1-a"
# исключаем тяжёлое и неперносимое: tessdata (на Ubuntu ставится apt'ом), receipts (их заново пришлём боту), кэши/логи
tar -czf bot.tar.gz --exclude='__pycache__' -C . src config deploy README.md data/positions.json data/seen_hashes.json data/api_quota.json
gcloud compute scp --project $PROJECT --zone $ZONE --tunnel-through-iap bot.tar.gz scp:bot.tar.gz
```

(вторая команда скопирует файл в домашнюю папку VM по IAP-туннелю)

На VM:

```bash
sudo mkdir -p /opt/receiptsbot
sudo tar -xzf ~/bot.tar.gz -C /opt/receiptsbot
sudo bash /opt/receiptsbot/deploy/oracle_install.sh
```

Установщик: поставит tesseract+русский, venv, создаст сервис `receipts-bot`
и cron (бэкап ежедневно 04:30 UTC, хранение 14 дней). На этом шаге бот стартует И НЕ
запустится до токенов — это норм, заполняем дальше.

---

## Этап 6. Токены и запуск

```bash
sudo nano /etc/receiptsbot.env
```
Впишите (без кавычек):
```
TELEGRAM_BOT_TOKEN=<токен от BotFather>
PROVERKACHEKA_TOKEN=<токен детализации>
```
Ctrl+O, Enter, Ctrl+X, затем:

```bash
sudo systemctl restart receipts-bot
journalctl -u receipts-bot -f
```
Ждём строку `[… ] Бот запущен. Ожидаю чеки…` → **Ctrl+C** (выйти из журнала).

В Telegram: `/status` у бота — должно прийти количество чеков (реестр уже залит).

---

## Этап 7. Остановить локального бота (двух не бывает)

PowerShell **от администратора**:
```powershell
schtasks /Delete /TN "ReceiptsBot" /F
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
```

Отныне: чеки → Telegram → бот на VM → CSV/дашборд там же; `/report` приходит файлом в чат.

---

## Повседневная эксплуатация

| Что | Как |
|---|---|
| Логи бота | `gcloud compute ssh receipts-bot --zone us-west1-a --tunnel-through-iap` → `journalctl -u receipts-bot -n 200` |
| Обновить код/справочник | на ПК: `tar -czf bot.tar.gz -C . src config` → `gcloud compute scp … bot.tar.gz scp:bot.tar.gz` → на VM: `sudo tar -xzf ~/bot.tar.gz -C /opt/receiptsbot && sudo chown -R bot:bot /opt/receiptsbot && sudo systemctl restart receipts-bot` |
| Забрать бэкап домой | `gcloud compute scp --tunnel-through-iap receipts-bot:/opt/backups/latest/data/positions.json .\data\positions.json` |
| Восстановить VM после аварии | пересоздать VM (этап 2–6), залить последний бэкап: положить tar c data/ в ~/ и `sudo tar -xzf … -C /opt/receiptsbot` |
| Заново запустить бота | `sudo systemctl restart receipts-bot` |
| Посмотреть бэкапы | `ls /opt/backups` |

---

## Проверка, что Always Free не превращается в счёт

- Billing → Reports в конце месяца: должно быть ~$0.00.
- Проверьте VM: External IP «None», диск «Standard», регион в списке трёх.
- Egress: боту хватает ~50–100 МБ/мес при лимите 1 ГБ.
- Бюджет $1 из этапа 1 — ваш детектор любых сюрпризов.

---

## Решение проблем

| Симптом | Причина/действие |
|---|---|
| «Out of host capacity» при создании | квоты free-регионов кончились — смените зону/регион, попробуйте позже |
| gcloud ssh: 403 Could not connect | IAP не включён или нет роли — этап «IAP»; проверьте Firewall-правило `allow-iap-ssh` |
| gcloud scp: «Permission denied» | не хватало прав service account для IAP-tunnel; проверить роль «IAP-secured Tunnel User» у себя |
| Бот падает (OOM) на OCR | swap не включён — см. этап 4; если повторяется — MAX_SIDE в prepare_receipts.py уменьшить до 1400 |
| Бот пишет «Не задан токен» | `/etc/receiptsbot.env` пустой/не перечитан — заполнить и `sudo systemctl restart` |
| Двух ботов конфликт (409) | локальный ещё жив — этап 7 |
| systemd не стартует | `journalctl -xeu receipts-bot` — обычно опечатка в env-файле |