# -*- coding: utf-8 -*-
"""Telegram-бот: принимает фото чеков и складывает их в receipts/.

Как пользоваться:
  1. Создайте бота у @BotFather и получите токен.
  2. Вставьте токен в config/telegram.json  ({"token": "123456:ABC..."})
     либо задайте переменную окружения TELEGRAM_BOT_TOKEN.
  3. Запустите:  python src/telegram_bot.py

Команды в чате с ботом:
  /start   — инструкция
  /status  — сколько чеков принято
  /report  — прислать готовый Excel-отчёт
  /run     — пересобрать отчёт из текущих данных

Фото/PDF, отправленные боту, сохраняются в receipts/, дубликаты отсекаются,
после каждого нового чека автоматически запускается конвейер обработки.
"""

import json
import os
import re
import subprocess
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_detail import msk_now, pending_count, quota_block_message, quota_snapshot  # noqa: E402
from ingest import ingest, load_registry, run_pipeline, seed_registry  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Корень ДАННЫХ: у бота с группами доступа он своя на группу (env DATA_ROOT, см. config/access.json);
# без групп (CLI-запуск) — корень проекта, как раньше.
ROOT = os.environ.get("DATA_ROOT") or CODE_ROOT
CONFIG = os.path.join(CODE_ROOT, "config", "telegram.json")
OFFSET_FILE = os.path.join(CODE_ROOT, "data", "telegram_offset.json")   # общий: один бот
LOG_FILE = os.path.join(CODE_ROOT, "logs", "telegram_bot.log")          # общий журнал
API_BASE = "https://api.telegram.org"


def log(message):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, flush=True)  # без flush буферизация съедает вывод в скрытый cmd
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def load_token():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if token:
        return token
    if os.path.exists(CONFIG):
        with open(CONFIG, encoding="utf-8") as fh:
            token = (json.load(fh).get("token") or "").strip()
    if not token or token.startswith("ВСТАВЬТЕ"):
        log("Не задан токен бота. Укажите его в config/telegram.json или в TELEGRAM_BOT_TOKEN.")
        sys.exit(1)
    return token


TOKEN = ""
API = ""
LOCK_FILE = os.path.join(CODE_ROOT, "data", "bot_pid.lock")


def pid_alive(pid, name_prefix="python"):
    """Живёт ли процесс с таким PID и с ли похожим именем (защита от устаревшего лока)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).ProcessName"],
            capture_output=True, text=True, timeout=15).stdout.strip()
        return out.lower().startswith(name_prefix[:6])
    except Exception:
        return False  # не сумели проверить — считаем мёртвым


def acquire_lock():
    """Один бот на машину: если живой экземпляр есть — выходим. Возвращает True, если лок захвачен."""
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, encoding="utf-8") as fh:
                pid = int((fh.read().split()[0]).strip())
            if pid_alive(pid):
                log(f"Бот уже запущен (PID {pid}) — этот экземпляр не нужен, выхожу")
                return False
            log(f"Лок устарел (PID {pid} мёртв) — перехватываю")
        except (ValueError, OSError):
            log("Лок повреждён — перезаписываю")
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    with open(LOCK_FILE, "w", encoding="utf-8") as fh:
        fh.write(f"{os.getpid()} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return True


def release_lock():
    try:
        with open(LOCK_FILE, encoding="utf-8") as fh:
            pid = int(fh.read().split()[0])
        if pid == os.getpid():
            os.remove(LOCK_FILE)
    except (OSError, ValueError):
        pass


# --- Сетевой слой: устойчив к unstable-маршруту ------------------------------
# Правило: urllib кэширует opener с системным прокси на весь процесс, и когда
# VPN/прокси-клиент ПК выключается, бот умирает в петле ConnectionRefused на 127.0.0.1.
# Поэтому: (1) прокси ТОЛЬКО явный из config/telegram.json ("proxy": "http://..."),
# (2) умный выбор пути «прямой <-> прокси» с запоминанием работающего,
# (3) серии отказов => свежий процесс (см. net_fail в main()).
DEFAULT_PROXY = ""          # может быть переопределён полем "proxy" в config/telegram.json
OPEN_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_PROXY_CACHE = {"value": None, "opener": None}


def get_proxy():
    """Прокси из config/telegram.json (поле proxy); читается лениво, кэшируется."""
    if _PROXY_CACHE["opener"] is not None:
        return _PROXY_CACHE["opener"]
    proxy = DEFAULT_PROXY
    try:
        with open(CONFIG, encoding="utf-8") as fh:
            proxy = (json.load(fh).get("proxy") or proxy).strip()
    except Exception:
        pass
    opener = (urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        if proxy else OPEN_DIRECT)
    _PROXY_CACHE.update({"value": proxy, "opener": opener})
    return opener


NET_MODE = {"ok": "direct"}   # последний рабочий путь: "direct" | "proxy"


def smart_open(url, timeout=70):
    """Открывает url, пробуя рабочий путь; сбой -> чередует прямой и прокси."""
    proxy = get_proxy()
    has_proxy = _PROXY_CACHE["value"] != ""
    order = [NET_MODE["ok"]]
    for other in ("proxy", "direct"):
        if other not in order and (other == "direct" or has_proxy):
            order.append(other)
    last_exc = None
    for mode in order:
        try:
            resp = (OPEN_DIRECT if mode == "direct" else proxy).open(url, timeout=timeout)
            if NET_MODE["ok"] != mode:
                log(f"Сетевой путь переключён: {mode}")
            NET_MODE["ok"] = mode
            return resp
        except Exception as exc:
            last_exc = exc
    raise last_exc


def api(method, params=None, timeout=70):
    url = f"{API}/{method}?" + urllib.parse.urlencode(params or {})
    with smart_open(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_post_file(method, params, field, file_path):
    """Отправка файла в Telegram (multipart/form-data)."""
    boundary = uuid.uuid4().hex
    body = bytearray()

    def add_field(name, value):
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    for key, value in (params or {}).items():
        add_field(key, value)

    filename = os.path.basename(file_path)
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode())
    body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
    with open(file_path, "rb") as fh:
        body.extend(fh.read())
    body.extend(f"\r\n--{boundary}--\r\n".encode())

    request = urllib.request.Request(f"{API}/{method}", data=bytes(body))
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with smart_open(request, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_message(chat_id, text):
    try:
        api("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"})
    except urllib.error.URLError as exc:
        log(f"Не удалось отправить сообщение: {exc}")


SENDERS_MAP_NAME = "senders_map.json"   # файл лежит в группе

STORE_BASE = os.path.join(CODE_ROOT, "groups")
ACCESS_FILE = os.path.join(CODE_ROOT, "config", "access.json")


def load_access():
    if os.path.exists(ACCESS_FILE):
        with open(ACCESS_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    return {"groups": {}, "unknown_policy": "reject"}


def sender_of(message):
    """Человекочитаемая подпись отправителя: username/имя + id чата."""
    person = (message.get("from") or {})
    handle = (person.get("username") or person.get("first_name") or "").replace("@", "")
    title = (message.get("chat") or {}).get("title")
    label = f"@{handle}" if handle else (title or "")
    return (label + f" [#{message['chat']['id']}]").strip()


def resolve_group(message):
    """Возвращает (код группы, подпись) или (None, None) для незнакомого аккаунта."""
    person = (message.get("from") or {})
    uid = person.get("id")
    uname = (person.get("username") or "").replace("@", "").lower()
    chat_id = message["chat"]["id"]
    for gcode, g in load_access().get("groups", {}).items():
        known_ids = g.get("users") or []
        if uid in known_ids or chat_id in known_ids:
            return gcode, g.get("label", gcode)
        if uname and uname in [u.lower() for u in (g.get("usernames") or [])]:
            return gcode, g.get("label", gcode)
    return None, None


def group_store(gcode):
    """Корень данных группы; создаёт папки при первом обращении."""
    store = os.path.join(STORE_BASE, gcode)
    for sub in ("data", "reports", "logs", "receipts", os.path.join("data", "prepared"),
                os.path.join("data", "incoming")):
        os.makedirs(os.path.join(store, sub), exist_ok=True)
    return store


def store_paths(store):
    return {
        "positions": os.path.join(store, "data", "positions.json"),
        "report": os.path.join(store, "reports", "Отчёт_затрат_2026.xlsx"),
        "dashboard": os.path.join(store, "reports", "Дашборд.html"),
        "senders": os.path.join(store, SENDERS_MAP_NAME),
        "manual_seq": os.path.join(store, "data", "manual_entry_seq.json"),
        "incoming": os.path.join(store, "data", "incoming"),
    }


def remember_sender(senders_path, name, sender):
    """Кто прислал файл: filename -> подпись (для отчёта группы)."""
    if not name:
        return
    try:
        mapping = {}
        if os.path.exists(senders_path):
            with open(senders_path, encoding="utf-8") as fh:
                mapping = json.load(fh)
        mapping[name] = sender
        with open(senders_path, "w", encoding="utf-8") as fh:
            json.dump(mapping, fh, ensure_ascii=False, indent=1)
    except Exception as exc:
        log(f"не сохранена подпись отправителя: {exc!r}")


def load_offset():
    if os.path.exists(OFFSET_FILE):
        with open(OFFSET_FILE, encoding="utf-8") as fh:
            return json.load(fh).get("offset", 0)
    return 0


def save_offset(offset):
    os.makedirs(os.path.dirname(OFFSET_FILE), exist_ok=True)
    with open(OFFSET_FILE, "w", encoding="utf-8") as fh:
        json.dump({"offset": offset}, fh)


def download_file(file_id, name, tmp_dir=None):
    info = api("getFile", {"file_id": file_id})
    if not info.get("ok"):
        return None, name
    file_path = info["result"]["file_path"]
    ext = os.path.splitext(file_path)[1] or ".jpg"
    target_name = name if name.lower().endswith(ext.lower()) else os.path.splitext(name)[0] + ext
    if not tmp_dir:
        tmp_dir = os.path.join(ROOT, "data", "incoming")
    os.makedirs(tmp_dir, exist_ok=True)
    target = os.path.join(tmp_dir, target_name)
    url = f"{API_BASE}/file/bot{TOKEN}/{file_path}"
    with smart_open(url, timeout=120) as resp, open(target, "wb") as out:
        out.write(resp.read())
    return target, target_name


def extract_attachment(message):
    """Возвращает (file_id, имя_файла) для фото или документа."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if message.get("photo"):
        biggest = max(message["photo"], key=lambda p: p.get("file_size", 0))
        return biggest["file_id"], f"tel_{stamp}.jpg"
    document = message.get("document")
    if document:
        mime = document.get("mime_type", "")
        if mime.startswith("image/") or mime == "application/pdf":
            return document["file_id"], document.get("file_name") or f"tel_{stamp}.pdf"
    return None, ""


MANUAL_SEQ_NAME = "manual_entry_seq.json"   # счётчик — общий, но запись в группе
RE_NUM_TOKEN = re.compile(r"^\d+([.,]\d+)?$")


def parse_manual_entry(text):
    """Разбирает «/add 500 за что», «500 за что» и разрядные пробелы («1 250,50 канцелярия»).

    Возвращает (amount, description) или None, если это не расход без чека.
    """
    body = text.strip()
    is_command = body.startswith("/")
    if is_command:
        parts = body.split(None, 1)
        if parts and parts[0].lower() not in ("/add", "/расход"):
            return None
        body = parts[1].strip() if len(parts) > 1 else ""
    if not body:
        return None if is_command else None

    tokens = body.split()
    numeric = []
    rest = tokens
    while rest and RE_NUM_TOKEN.match(rest[0]):
        numeric.append(rest.pop(0))
    if not numeric:
        return None
    amount_text = "".join(numeric).replace(" ", "")
    if len(numeric) > 1:
        # два числовых токена подряд сливаем как «1 250,50», но не как «2 пива»
        second = numeric[1]
        if not ("," in second or "." in second or len(second) >= 4):
            rest = [second] + rest
            amount_text = numeric[0]
    if rest:
        what = " ".join(rest)
    else:
        what = ""
    what = re.sub(r"\s+", " ", what).strip(" -—")
    try:
        amount = float(amount_text.replace(",", "."))
    except ValueError:
        return None
    return (amount, what) if amount > 0 else None


def next_manual_id(seq_file):
    seq = 1
    if os.path.exists(seq_file):
        with open(seq_file, encoding="utf-8") as fh:
            seq = json.load(fh).get("seq", 0) + 1
    os.makedirs(os.path.dirname(seq_file), exist_ok=True)
    with open(seq_file, "w", encoding="utf-8") as fh:
        json.dump({"seq": seq}, fh)
    return f"M-{seq:03d}"


def add_manual_entry(positions_path, seq_file, amount, what, sender=""):
    """Записывает расход без чека как псевдочек (file=""). Возвращает (rid, код статьи)."""
    with open(positions_path, encoding="utf-8") as fh:
        payload = json.load(fh)
    payload.setdefault("receipts", [])
    payload.setdefault("items", [])
    rid = next_manual_id(seq_file)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    shop = f"(без чека): {what[:60]}"
    payload["receipts"].append({
        "receipt_id": rid, "file": "", "datetime": stamp, "shop": shop,
        "inn": "", "total": round(amount, 2), "method": "manual-entry",
        "note": f"Внесено ботом без чека: {what[:80]}", "sender": sender,
        "fiscal": {},
    })
    import build_report as br
    cats, uncls = br.load_categories()
    code, _kw = br.classify(what, cats)
    item = {"receipt_id": rid, "name": what, "qty": 1,
            "price": round(amount, 2), "sum": round(amount, 2)}
    item["category"] = code if code else uncls["code"]
    payload["items"].append(item)
    with open(positions_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return rid, (code if code else uncls["code"]), cats, uncls


def status_text(store, label=None):
    paths = store_paths(store)
    registry = seed_registry(load_registry())
    snap = quota_snapshot()
    quota_line = (f"Запросов детализации (общий лимит сервиса): {snap['used']} из {snap['limit']}, "
                  f"осталось {snap['remaining']}")
    if snap["remaining"] <= 0:
        quota_line += " — приём новых чеков приостановлен до возобновления лимита"
    n_receipts = 0
    if os.path.exists(paths["positions"]):
        with open(paths["positions"], encoding="utf-8") as fh:
            n_receipts = len(json.load(fh).get("receipts", []))
    return (f"Группа: {label or os.path.basename(store)}\n"
            f"Чеков в группе: {n_receipts} (файлов принято через бота: {len(registry)})\n"
            f"{quota_line}\n"
            f"Отчёт группы: {paths['report']}")


def handle_message(message):
    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()

    gcode, glabel = resolve_group(message)
    if gcode is None:
        log(f"Отказ в доступе: chat {chat_id} не входит ни в одну группу")
        person = (message.get("from") or {})
        uname = person.get("username") or ""
        send_message(chat_id,
                     "У этого бота доступ по группам — ваш аккаунт не числится, чеки не принимаются.\n\nЧтобы вас добавили, передайте владельцу бота:\n"
                     f"chat_id = {chat_id}"
                     + (f"\nusername = @{uname}" if uname else ""))
        return
    store = group_store(gcode)
    paths = store_paths(store)
    sender = sender_of(message)
    log(f"[{gcode}] от {sender}: {(text or '<медиа>')[:60]}")

    if text == "/start":
        send_message(chat_id,
                     f"Группа «{glabel}». Пришлите фото чека или PDF — сохраню в папку группы и пересоберу её отчёт.\n\n"
                     "Расход без чека: напишите сумму и за что:\n"
                     "/add 500 обед при встрече — или просто «500 обед при встрече»\n\n"
                     "Команды:\n/status — сколько чеков в группе\n/report — прислать Excel группы\n"
                     "/dash — прислать HTML-дашборд группы\n/run — пересобрать отчёт группы")
        return

    manual = parse_manual_entry(text)
    if manual:
        amount, what = manual
        if not what or len(what) < 3:
            send_message(chat_id, "Укажите, за что расход. Формат: /add 500 обед при встрече")
            return
        if amount > 10_000_000:
            send_message(chat_id, "Слишком большая сумма — проверьте число.")
            return
        send_message(chat_id, f"Записываю расход без чека: {amount:.2f} ₽ — {what}")
        try:
            rid, code, cats, uncls = add_manual_entry(paths["positions"], paths["manual_seq"],
                                                      amount, what, sender=sender)
        except Exception as exc:
            log(f"Ошибка записи расхода без чека: {exc!r}")
            send_message(chat_id, "Не получилось записать — попробуйте ещё раз.")
            return
        article = next((c["name"] for c in cats if c["code"] == code), None)
        if not article:
            article = "без статьи (лист «Требует разметки»)"
        run_pipeline(data_root=store)
        send_message(chat_id, f"Принято: {rid} · {amount:.2f} ₽ — {what}\n"
                              f"Статья: {article}\n\n/report — прислать Excel")
        return

    if text == "/dash":
        if not os.path.exists(paths["dashboard"]):
            send_message(chat_id, "Дашборда пока нет — запускаю сборку…")
        run_pipeline(data_root=store)
        if os.path.exists(paths["dashboard"]):
            api_post_file("sendDocument", {"chat_id": chat_id}, "document", paths["dashboard"])
        else:
            send_message(chat_id, "Не удалось собрать дашборд — смотрите логи.")
        return

    if text == "/status":
        send_message(chat_id, status_text(store, glabel))
        return

    if text == "/run":
        send_message(chat_id, f"Пересобираю отчёт группы «{glabel}»…")
        run_pipeline(data_root=store)
        send_message(chat_id, "Отчёт обновлён. /report — прислать Excel")
        return

    if text == "/report":
        send_message(chat_id, f"Собираю свежий Excel-отчёт группы «{glabel}»…")
        build = os.path.join(CODE_ROOT, "src", "build_report.py")
        proc = subprocess.run([sys.executable, build, "--input", paths["positions"],
                               "--output", paths["report"]],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace",
                              env=dict(os.environ, DATA_ROOT=store,
                                       PYTHONIOENCODING="utf-8", PYTHONUTF8="1"))
        if proc.returncode == 0 and os.path.exists(paths["report"]):
            api_post_file("sendDocument", {"chat_id": chat_id}, "document", paths["report"])
            return
        csv_path = os.path.splitext(paths["report"])[0] + ".csv"
        if os.path.exists(csv_path):
            send_message(chat_id, "Excel не записался (файл занят Excel'ем) — присылаю CSV-снимок:")
            api_post_file("sendDocument", {"chat_id": chat_id}, "document", csv_path)
        else:
            send_message(chat_id, "Отчёт не получился — см. logs/telegram_bot.log.")
        return

    file_id, name = extract_attachment(message)
    if not file_id:
        send_message(chat_id, "Пришлите фото чека или PDF-файл.")
        return

    # Лимит исчерпан: чек принимаем в очередь, детализация — после возобновления
    blocked = quota_snapshot()["remaining"] <= 0

    try:
        tmp, name = download_file(file_id, name, tmp_dir=paths["incoming"])
    except urllib.error.URLError as exc:
        log(f"Ошибка загрузки файла: {exc}")
        send_message(chat_id, "Не удалось скачать файл, попробуйте ещё раз.")
        return

    result = ingest(tmp, source="telegram", move=True, name=name,
                    receipts_dir=os.path.join(store, "receipts"))
    status = result["status"]
    remember_sender(paths["senders"], result.get("name"), sender)
    log(f"[{gcode}] файл: {status} — {result.get('name', '')}")

    if status == "added":
        if blocked:
            log(f"Лимит исчерпан: чек {result['name']} сохранён в очередь на обработку")
            send_message(chat_id, f"Чек принят: {result['name']}\n\n" + quota_block_message())
            return
        send_message(chat_id, f"Чек принят: {result['name']}\nОбрабатываю…")
        proc = run_pipeline(data_root=store)
        summary = (proc.stdout or "").strip().splitlines()
        tail = " · ".join(line for line in summary[-4:] if line)
        reply = f"Готово.\n{tail}\n\n/report — прислать Excel"
        snap = quota_snapshot()
        if any("ЗАБЛОКИРОВАНА" in line for line in summary):
            reply += "\n\n" + quota_block_message()
        elif snap["remaining"] <= 2:
            reply += (f"\n\n⚠️ Осталось бесплатных запросов детализации: {snap['remaining']} (из {snap['limit']})."
                      f" По исчерпании чеки будут ставиться в очередь до {snap['resume']} (00:00 МСК).")
        send_message(chat_id, reply)
    elif status == "duplicate":
        send_message(chat_id, f"Этот чек уже был ({result.get('name')}) — пропустил, чтобы не задваивать.")
    else:
        send_message(chat_id, f"Файл не принят: {result.get('reason', status)}")


def main():
    global TOKEN, API
    TOKEN = load_token()
    API = f"{API_BASE}/bot{TOKEN}"
    if not acquire_lock():
        return
    log("Бот запущен. Ожидаю чеки…")
    net_fail = 0       # счётчик подряд идущих сбоев сети
    offset = load_offset()
    quota_date = quota_snapshot()["date"]
    try:
        while True:
            # «Ночной будильник»: лимит возобновился — обрабатываем очереди ВСЕХ групп
            today = msk_now().strftime("%Y-%m-%d")
            if today != quota_date:
                quota_date = today
                for gcode in load_access().get("groups", {}):
                    store_ = group_store(gcode)
                    paths_ = store_paths(store_)
                    waiting = pending_count(paths_["positions"])
                    if waiting:
                        log(f"[{gcode}] лимит детализации возобновился; чеков в очереди: {waiting} — обрабатываю")
                        run_pipeline(data_root=store_)
            try:
                updates = api("getUpdates", {"offset": offset, "timeout": 30})
            except KeyboardInterrupt:
                log("Остановлено пользователем.")
                break
            except Exception as exc:
                # RemoteDisconnected / таймауты / прочие сетевые капризы.
                # Серии отказов (заложенный сокет/DNS долгоживущего процесса) лечатся
                # СВЕЖИМ процессом: после порога выходим — run_bot.bat поднимет через 30 с.
                net_fail += 1
                log(f"Сбой сети или API ({net_fail} подряд): {exc!r}. Повтор через 10 с.")
                if net_fail >= 20:
                    log("Сеть не отвечает 20 раз подряд — перезапускаюсь (чистый процесс)")
                    sys.exit(1)
                time.sleep(10)
                continue

            net_fail = 0   # успех — счётчик сбоев обнуляется
            for update in updates.get("result", []):
                offset = update["update_id"] + 1
                save_offset(offset)
                message = update.get("message") or update.get("edited_message")
                if message:
                    try:
                        handle_message(message)
                    except Exception as exc:  # бот не должен падать из-за одного сообщения…
                        log(f"Ошибка обработки сообщения: {exc!r}")
                        # …но пользователь обязан узнать, что его сообщение потерялось
                        try:
                            send_message(message["chat"]["id"],
                                         "Не получилось обработать сообщение (сбой внутри — записал в журнал). "
                                         "Попробуйте прислать ещё раз.")
                        except Exception:
                            pass
    finally:
        release_lock()


if __name__ == "__main__":
    main()
