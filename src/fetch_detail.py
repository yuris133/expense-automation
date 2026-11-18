# -*- coding: utf-8 -*-
"""Детализация позиций чеков по фискальным признакам через сервис проверки чеков.

README, «Как извлекаются данные из чеков» п.1: QR-код даёт фискальные признаки
(дата, сумма, ФН/ФД/ФП); этим модулем у сервиса (proverkacheka.com — обёртка над
API ФНС) запрашивается официальная детализация по позициям.

Логика:
  · ищем чеки из positions.json, у которых есть ФН/ФД/ФП, но НЕТ позиций;
  · qrraw берём из data/qr_raw.json, если файл чека там есть, иначе собираем сами;
  · позиций не находим (чек ИП «только итог») — помечаем чек, не теряем его;
  · запросы ограничены: на бесплатном тарифе ~15 в сутки, сверх не ходим.

Настройка: токен из личного кабинета https://proverkacheka.com вписывается
в config/proverkacheka.json (поле api_token) или в переменную окружения
PROVERKACHEKA_TOKEN.

Запуск: python src/fetch_detail.py [--dry]
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Корень ДАННЫХ: у бота с группами доступа он своя на группу (env DATA_ROOT, см. config/access.json);
# без групп (CLI-запуск) — корень проекта, как раньше.
ROOT = os.environ.get("DATA_ROOT") or CODE_ROOT
POSITIONS = os.path.join(ROOT, "data", "positions.json")
QR_RAW = os.path.join(ROOT, "data", "qr_raw.json")
CONFIG = os.path.join(CODE_ROOT, "config", "proverkacheka.json")
QUOTA_FILE = os.path.join(CODE_ROOT, "data", "api_quota.json")   # квота общая: тариф сервиса один на токен
DAILY_LIMIT = 15
RETRY_CODES = {"3"}  # «чек ещё в обработке» — повторяем
MARKER_BLOCKED = "API-ДЕТАЛИЗАЦИЯ ЗАБЛОКИРОВАНА: бесплатно израсходованы все запросы"
MARKER_LEFT = "API-запросов осталось"  # префикс строки «...: N» для бота


def msk_now():
    """Текущий московский момент (UTC+3) — лимит тарифа отсчитывается посуточно."""
    return datetime.now(timezone.utc) + timedelta(hours=3)


def load_quota():
    if os.path.exists(QUOTA_FILE):
        with open(QUOTA_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def save_quota(q):
    os.makedirs(os.path.dirname(QUOTA_FILE), exist_ok=True)
    with open(QUOTA_FILE, "w", encoding="utf-8") as fh:
        json.dump(q, fh, ensure_ascii=False, indent=1)


def quota_today(q=None):
    now = msk_now()
    today = now.strftime("%Y-%m-%d")
    q = q or load_quota()
    if q.get("date") != today:
        q = {"date": today, "used": 0}
    return q, today


def quota_snapshot():
    """Для бота: (использовано, лимит, осталось, дата_московская, дата_возобновления)."""
    q, today = quota_today()
    used = int(q.get("used", 0))
    resume = msk_now() + __import__("datetime").timedelta(days=1)
    return {"used": used, "limit": DAILY_LIMIT,
            "remaining": max(DAILY_LIMIT - used, 0),
            "date": today,
            "resume": resume.strftime("%d.%m.%Y")}


def quota_block_message():
    snap = quota_snapshot()
    return (f"⛔ Бесплатные запросы детализации израсходованы ({snap['limit']}/{snap['limit']}).\n"
            f"Работа бота возобновится после 00:00 (МСК), ориентировочно {snap['resume']}.\n"
            f"Чек сохранён и будет обработан автоматически после возобновления лимита.\n"
            f"Команды /status и /report продолжат работать.")


def pending_count(positions_path=None):
    """Сколько чеков группы ждут детализации (позиций нет)."""
    positions_path = positions_path or POSITIONS
    if not os.path.exists(positions_path):
        return 0
    with open(positions_path, encoding="utf-8") as fh:
        payload = json.load(fh)
    counted = sum(1 for r in payload.get("receipts", [])
                  if (r.get("fiscal") or {}).get("fn")
                  and not any(it.get("receipt_id") == r["receipt_id"]
                              for it in payload.get("items", [])))
    return counted


def spend():
    """Списывает один запрос; возвращает снапшот после списания или None, если лимит исчерпан."""
    q, today = quota_today()
    if int(q["used"]) >= DAILY_LIMIT:
        return None
    q["used"] += 1
    save_quota(q)
    return quota_snapshot()


def load_config():
    env_token = os.environ.get("PROVERKACHEKA_TOKEN", "").strip()
    token, base_url = "", "https://proverkacheka.com/api/v1/check/get"
    if os.path.exists(CONFIG):
        with open(CONFIG, encoding="utf-8") as fh:
            data = json.load(fh)
        token = data.get("api_token", "").strip()
        base_url = data.get("base_url", base_url)
    if not token or env_token:
        token = env_token
    return token, base_url


def to_rubles(price, qty, total_sum, expected=None):
    """ФНС отдаёт деньги в копейках; приводим к рублям по сходимости.

    Правило простое и устойчивое: цена×кол-во сравнивается по ОТЕЧЕСТВУ
    (итог чека из QR в рублях — первичная шкала, потом сумма позиции).
    Совпало с якорем — рубли; в 100 раз больше — копейки.
    """
    if price is None:
        return price, round(float(total_sum), 2) if total_sum else None
    pq = float(price) * float(qty)
    scale = 1
    for anchor in (expected, total_sum):
        if not anchor:
            continue
        ratio = pq / float(anchor)
        if abs(ratio - 1) <= 0.02:      # уже в рублях
            break
        if abs(ratio - 100) <= 0.6:     # копейки
            scale = 100
            break
    price_out = round(float(price) / scale, 2)
    sum_out = round(float(total_sum) / scale, 2) if total_sum else round(price_out * float(qty), 2)
    return price_out, sum_out


def fix_total_scale(total, items_sum, expected=None):
    if total is None:
        return None
    total = float(total)
    if items_sum and abs(items_sum - total) > 0.05 and abs(items_sum * 100 - total) < 0.05:
        return round(total / 100, 2)
    if expected and abs(expected - total) > 0.05 and abs(expected * 100 - total) < 0.05:
        return round(total / 100, 2)
    return round(total, 2)


def query(base_url, token, qrraw, attempts=3):
    """Возвращает ('ok', json) | ('blocked', None) | ('error', json|None).

    Каждый фактический HTTP-запрос списывает квоту (включая повторы «в обработке»).
    """
    headers = {"Authorization": f"Token {token}", "Content-Type": "application/json"}
    for i in range(attempts):
        snap = spend()
        if snap is None:
            print(MARKER_BLOCKED)
            return "blocked", None
        print(f"    запрос #{snap['used']} из {snap['limit']}, осталось {snap['remaining']}")
        try:
            resp = requests.post(base_url, headers=headers,
                                 json={"qrraw": qrraw, "token": token, "qr": "1"}, timeout=60)
        except requests.RequestException as exc:
            print(f"    сеть/сервис недоступны: {exc!r}")
            return "error", None
        if resp.status_code == 200:
            payload = resp.json()
            if str(payload.get("code")) in RETRY_CODES and i < attempts - 1:
                print("    чек ещё в обработке сервиса — повтор через 5 с")
                time.sleep(5)
                continue
            if snap["remaining"] <= 2:
                print(f"{MARKER_LEFT}: {snap['remaining']} — детализация скоро станет недоступна")
            return "ok", payload
        print(f"    HTTP {resp.status_code}: {resp.text[:200]}")
        return "error", None
    return "error", None


def extract_items(payload, expected=None):
    """Берёт позиции из ответа (data.json.items/positions).

    Шкала (копейки/рубли) определяется по ЧЕКУ ЦЕЛИКОМ: итог из QR (expected)
    сверяется с итогом ответа или с суммой позиций; затем масштаб применяется
    ко всем позициям одинаково.
    """
    data = (payload or {}).get("data") or {}
    inner = data.get("json") or {}
    rows = inner.get("items") or inner.get("positions") or []
    raw_items = []
    for row in rows:
        name = (row.get("name") or row.get("n") or "").strip()
        if not name:
            continue
        qty = float(row.get("quantity") or row.get("q") or 1)
        price = row.get("price") or row.get("p")
        sum_raw = row.get("sum") or row.get("s")
        raw_items.append({"qty": qty, "price": price, "sum": sum_raw, "name": name})
    total_raw = inner.get("totalSum") or inner.get("total")

    def scale_of(value):
        if not expected or not value:
            return None
        ratio = float(value) / float(expected)
        if abs(ratio - 1) <= 0.02:
            return 1
        if abs(ratio - 100) <= 0.6:
            return 100
        return None

    scale = 1
    if raw_items:
        s_all = sum(float(i["sum"]) if i["sum"] else (float(i["price"] or 0) * i["qty"])
                    for i in raw_items)
        scale = scale_of(total_raw) or scale_of(round(s_all, 2)) or 1
    items = []
    for it in raw_items:
        price = float(it["price"] or 0) / scale
        item_sum = (float(it["sum"]) / scale) if it["sum"] else round(price * it["qty"], 2)
        items.append({"name": it["name"], "qty": round(it["qty"], 3),
                      "price": round(price, 2), "sum": round(item_sum, 2)})
    items_sum = round(sum(i["sum"] for i in items), 2) if items else None
    total = fix_total_scale(total_raw, items_sum, expected)
    return items, total


def main():
    parser = argparse.ArgumentParser(description="Детализация чеков через API проверки чеков")
    parser.add_argument("--dry", action="store_true", help="не сохранять, только показать")
    args = parser.parse_args()

    token, base_url = load_config()
    if not token or token.startswith("ВСТАВЬТЕ"):
        print("API проверки чеков не настроен (config/proverkacheka.json: api_token) — пропуск шага")
        return

    if not os.path.exists(POSITIONS):   # пустой группе нечего детализировать
        print("Позиций в группе ещё нет — API-детализация не нужна")
        return
    with open(POSITIONS, encoding="utf-8") as fh:
        payload = json.load(fh)
    qr_raw_map = {}
    if os.path.exists(QR_RAW):
        with open(QR_RAW, encoding="utf-8") as fh:
            qr_raw_map = {e.get("file"): (e.get("qr") or {}).get("raw") for e in json.load(fh)}

    def has_items(rid):
        return any(it.get("receipt_id") == rid for it in payload.get("items", []))

    snap = quota_snapshot()
    if snap["remaining"] <= 0:
        print(MARKER_BLOCKED + f" (возобновление ~{snap['resume']}, 00:00 МСК)")
        return

    targets = [r for r in payload.get("receipts", [])
               if (r.get("fiscal") or {}).get("fn") and not has_items(r["receipt_id"])]
    targets = targets[:DAILY_LIMIT]
    if not targets:
        print("Детализация через API не нужна: все чеки с фискальными признаками уже с позициями")
        print(f"{MARKER_LEFT}: {snap['remaining']} (из {snap['limit']})")
        return
    targets = targets[:max(snap["remaining"], 0)]
    print(f"Чеков для API-детализации: {len(targets)} (осталось запросов: {snap['remaining']})")

    added = 0
    for rec in targets:
        rid = rec["receipt_id"]
        qrraw = qr_raw_map.get(rec.get("file") or "")
        if not qrraw:
            fiscal = rec.get("fiscal") or {}
            dt = (rec.get("datetime") or "").replace("-", "").replace(":", "").replace(" ", "T")
            dt = (dt + "00")[:13] if len(dt) == 12 else dt  # 20261016T1506(0)0
            if not (dt and fiscal.get("fn")):
                print(f"{rid}: не хватает признаков — пропуск")
                continue
            total = rec.get("total") or ""
            qrraw = f"t={dt}&s={total}&fn={fiscal['fn']}&i={fiscal.get('fd','')}&fp={fiscal.get('fp','')}&n=1"
        print(f"{rid}: qrraw={qrraw}")
        status, payload_resp = query(base_url, token, qrraw)
        if status == "blocked":
            break
        if status != "ok":
            continue
        code = str(payload_resp.get("code"))
        if code != "1":
            print(f"    сервис вернул код {code}: {payload_resp.get('data') or payload_resp.get('message')}")
            continue
        # итог чека из QR — якорь для распознавания копеек/рублей
        items, api_total = extract_items(payload_resp, expected=rec.get("total"))
        if not items:
            print("    детализации нет (вероятно, чек ИП без позиций)")
            rec.setdefault("note", "API: детализации нет — только общий итог")
            continue
        detected = round(sum(it["sum"] for it in items), 2)
        rec["method"] = str(rec.get("method", "qr")) + "+api"
        rec["note"] = (f"Позиции получены через API проверки чеков ({len(items)} поз.); "
                       f"итог чека {api_total if api_total else rec.get('total')}")
        for it in items:
            it["receipt_id"] = rid
            it["comment"] = "позиции из официальной детализации (API ФНС через сервис)"
        payload["items"].extend(items)
        added += 1
        print(f"    получено позиций: {len(items)} на {detected} ₽ (итог {api_total})")

    if args.dry:
        print("(--dry — не сохранено)")
        return
    if added:
        with open(POSITIONS, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"API: чеков с позициями добавлено: {added}")


if __name__ == "__main__":
    main()