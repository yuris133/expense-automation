# Чек-бот на Oracle Cloud Always Free — шпаргалка

Полная пояснённая версия в README.md проекта, раздел «Хостинг на Oracle Cloud».

## Создание VM в консоли Oracle (браузер)

1. cloud.oracle.com → Free Tier → регистрация (международная карта, $1 — вернут).
2. Compute → Instances → Create Instance:
   - Image: **Ubuntu 24.04** (Minimal);
   - Shape: **Ampere A1.Flex**, 2 OCPU, 12 GB RAM;
   - SSH-ключи: вставьте публичный ключ (создать: `ssh-keygen -t ed25519`);
   - Public IP: Assign.
3. Если «Out of host capacity» — меняйте Availability Domain / пробуйте позже.

## Разрыв с ПК → сервер (PowerShell)

```powershell
$IP = "<public-ip>"; $KEY = "$HOME\.ssh\id_ed25519"   # ваш ключ
tar -czf bot.tar.gz --exclude='__pycache__' -C "<где-то на диске>\...\Автоматизация чеков" src config deploy tessdata data README.md
scp -i $KEY bot.tar.gz ubuntu@${IP}:/tmp/
ssh -i $KEY ubuntu@$IP
```

на сервере:

```bash
sudo mkdir -p /opt/receiptsbot
sudo tar -xzf /tmp/bot.tar.gz -C /opt/receiptsbot
sudo bash /opt/receiptsbot/deploy/oracle_install.sh
sudo nano /etc/receiptsbot.env       # впишите оба токена
sudo systemctl restart receipts-bot
journalctl -u receipts-bot -f        # «Бот запущен. Ожидаю чеки…»
```

## После переезда

- Windows: `schtasks /Delete /TN "ReceiptsBot" /F` (права админа) — локальный экземпляр не нужен.
- Бэкап локально: `scp -i $KEY -r ubuntu@$IP:/opt/backups/latest/data .\data`

## Обновления после правок кода

```powershell
tar -czf bot.tar.gz -C "...\Автоматизация чеков" src config
scp -i $KEY bot.tar.gz ubuntu@${IP}:/tmp/
ssh -i $KEY ubuntu@$IP "sudo tar -xzf /tmp/bot.tar.gz -C /opt/receiptsbot && sudo chown -R bot:bot /opt/receiptsbot && sudo systemctl restart receipts-bot"
```

## Управление

```bash
sudo systemctl status  receipts-bot | restart | stop
sudo journalctl -u receipts-bot -n 200
cat /opt/backups/latest/date-stamp 2>/dev/null; ls /opt/backups
```