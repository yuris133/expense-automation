# -*- coding: utf-8 -*-
"""Разбор электронных чеков из PDF-вложений по текстовому слою.

README обещает: «Электронные чеки (PDF/HTML из письма): парсятся напрямую».
Этот модуль реализует шаг: по каждому PDF в receipts/, которого ещё нет в
data/positions.json, извлекаются позиции, дата, магазин, ИНН и итог.
Чек добавляется в positions.json автоматически (method="pdf").

Если у PDF нет текстового слоя (скан) — файл пропускается с сообщением;
такие чеки разбираются вручную или через OCR.
"""

import json
import os
import re
import sys

import pymupdf

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Корень ДАННЫХ: у бота с группами доступа он своя на группу (env DATA_ROOT, см. config/access.json);
# без групп (CLI-запуск) — корень проекта, как раньше.
ROOT = os.environ.get("DATA_ROOT") or CODE_ROOT
POSITIONS = os.path.join(ROOT, "data", "positions.json")
DUPLICATES = os.path.join(ROOT, "data", "pdf_duplicates.json")

MONEY = r"\d[\d\s]*[.,]\d{2}"
RE_ITEM_NO = re.compile(r"^\s*(\d+)\.\s*(.*)$")
RE_QTY_X_PRICE = re.compile(rf"^\s*(?P<qty>\d+(?:[.,]\d+)?)\s*[xх]\s*(?P<price>{MONEY})\s*$")
RE_SUM = re.compile(rf"^≡\s*(?P<sum>{MONEY})\s*$")
RE_TOTAL = re.compile(r"^\s*ИТОГ", re.IGNORECASE)
RE_MONEY_ANY = re.compile(MONEY)
RE_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}[:.]\d{2})")
RE_INN = re.compile(r"ИНН[:\s]*([\d]{10})")

NOISE = ("в т.ч.", "вид налогообложени", "инн продавца", "сайт", "кнт:", "сно:", "рн ккт",
         "фд:", "фпд:", "фн:", "предварительная оплата", "безналичными", "наличными",
         "кассовый чек", "https://", "адрес для проверки")
SHOP_ENDS = re.compile(r"\s*(ООО|ИП|АО|ОАО|ЗАО|ПАО)\s*«?.*«?\S*$", re.IGNORECASE)


def to_float(text):
    cleaned = re.sub(r"\s", "", text).replace(",", ".")
    return round(float(cleaned), 2)


def _extract_dt_from_text(text):
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}:\d{2})", re.sub(r"\s+", " ", text))
    if not m:
        return ""
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)} {m.group(4)}"


def _extract_shop_from_text(text):
    flat = re.sub(r"\s+", " ", text)
    m = re.search(r"(ООО\s*«[^»”]{2,60}»)\s*ИНН", flat)
    if not m:
        m = re.search(r'ООО\s*"([^"]{2,60})"\s*ИНН', flat)
    if not m:
        m = re.search(r"([А-ЯЁ][А-ЯЁа-яё\s-]{2,40})\s*ИНН", flat)
    if m:
        shop = m.group(1).strip()[:80]
        return shop if re.search(r"[А-ЯЁа-яёA-Z]", shop) else "(магазин не распознан)"
    return "(магазин не распознан)"


def parse_receipt_pdf(path):
    """Возвращает dict {datetime, shop, inn, total, items} или None, если позиций нет."""
    doc = pymupdf.open(path)
    lines = []
    for page in doc:
        lines.extend(page.get_text().splitlines())
    doc.close()
    if not any(line.strip() for line in lines):
        return None

    items, dt, inn, total, shop = [], None, "", None, ""
    pending = None  # {"name": …, "qty": …, "price": …} — ждём строку «≡ сумма»

    for raw in lines:
        line = raw.strip()
        if not line or line in ("Приход", "ФП", "РФП"):
            continue
        low = line.lower()
        if RE_TOTAL.match(line):
            continue  # итог собираем отдельным проходом
        if any(low.startswith(n) for n in NOISE):
            continue

        match = RE_QTY_X_PRICE.match(line)
        if match and pending is not None:
            pending["qty"] = to_float(match.group("qty"))
            pending["price"] = to_float(match.group("price"))
            continue

        match = RE_SUM.match(line)
        if match and pending is not None and "price" in pending:
            pending["sum"] = to_float(match.group("sum"))
            items.append(pending)
            pending = None
            continue

        match = RE_ITEM_NO.match(line)
        if match and not RE_MONEY_ANY.search(match.group(2)):
            name = match.group(2).strip()
            pending = {"name": name} if name else {"name": ""}
            continue
        if re.fullmatch(r"\d+\.", line):
            pending = {"name": ""}  # «1.» отдельной строкой — имя на следующей
            continue

        if pending is not None and not pending["name"] and len(line) >= 3 \
                and not RE_MONEY_ANY.search(line) and re.search(r"[А-Яа-яA-Za-z«»]", line):
            pending["name"] = line
            continue

        match = RE_INN.search(line)
        if match and not inn:
            inn = match.group(1)
            continue
        match = RE_DATE.search(line)
        if match and dt is None:
            dt = f"{match.group(3)}-{match.group(2)}-{match.group(1)} {match.group(4).replace('.', ':')}"[:16]
            continue
        if not shop and re.search(r"[«\"]?\s*ООО$|[«\"]?\s*(ИП|АО|ОАО|ЗАО|ПАО)$", line) \
                and not RE_MONEY_ANY.search(line) and len(line) < 80:
            shop = line
            continue
        if not shop and re.match(r"^(ООО|ИП|АО|ОАО|ЗАО|ПАО)\b", line) and not RE_MONEY_ANY.search(line):
            shop = line[:100]

    if pending is not None and "qty" in pending and "price" in pending:
        pending.setdefault("sum", round(pending["price"] * pending["qty"], 2))
        items.append(pending)
    items = [it for it in items if it.get("name")]

    if total is None:
        for i, raw in enumerate(lines):
            if RE_TOTAL.match(raw.strip()) and "НДС" not in raw.upper():
                for j in (i, i + 1, i + 2):
                    if j >= len(lines):
                        break
                    match = RE_SUM.search(lines[j].strip())
                    if match:
                        total = to_float(match.group("sum"))
                        break
                    match = RE_MONEY_ANY.search(lines[j].strip())
                    if match:
                        total = to_float(match.group())
                        break
                if total is not None:
                    break
    if items and total is None:
        total = round(sum(it["sum"] for it in items), 2)
    if not items:
        return None
    return {"datetime": dt or "", "shop": shop or "(магазин не распознан)", "inn": inn,
            "total": total, "items": items}


def fingerprint(parsed):
    """Сигнатура чека (магазин, итог, перечень позиций) — для нахождения повторных вложений."""
    names = tuple(sorted(it["name"] for it in parsed["items"]))
    return (parsed["shop"], round(float(parsed["total"] or 0), 2), names)


def existing_fingerprints(payload):
    """Сигнатуры уже учтённых чеков с позициями."""
    sums = {}
    for it in payload.get("items", []):
        sums.setdefault(it["receipt_id"], []).append(it.get("name", ""))
    result = {}
    for rec in payload.get("receipts", []):
        rid = rec["receipt_id"]
        if rid in sums:
            result[rid] = (rec.get("shop"), round(float(rec.get("total") or 0), 2),
                           tuple(sorted(sums[rid])))
    return result


def fiscal_from_text(text):
    """ФД/ФПД/ФН и итог из текстового слоя PDF любого формата."""
    flat = re.sub(r"\s+", " ", text)
    out = {}
    m = re.search(r"\bФН:\s*(\d{12,20})", flat)
    if m: out["fn"] = m.group(1)
    m = re.search(r"ИНН\s+(\d{10})\b", flat)
    if m: out["inn"] = m.group(1)
    m = re.search(r"\bФД:\s*(\d{1,10})", flat)
    if m: out["fd"] = m.group(1)
    m = re.search(r"\bФПД:\s*(\d{5,15})", flat)
    if m: out["fp"] = m.group(1)
    m = re.search(r"Итого\s*[:\s]*([\d\s]+[.,]\d{2})", flat, re.IGNORECASE)
    total = None
    if m:
        total = round(float(m.group(1).replace(" ", "").replace(",", ".")), 2)
    return out, total


def run_auto():
    if not os.path.exists(POSITIONS):
        print("positions.json не найден — сначала запустите prepare")
        return
    with open(POSITIONS, encoding="utf-8") as fh:
        payload = json.load(fh)
    known = {r.get("file") for r in payload.get("receipts", [])}
    dup_registry = {}
    if os.path.exists(DUPLICATES):
        with open(DUPLICATES, encoding="utf-8") as fh:
            dup_registry = json.load(fh)
    _orig_registry = dict(dup_registry)
    known_prints = set(existing_fingerprints(payload).values())
    receipts_dir = os.path.join(ROOT, "receipts")
    added = 0
    dupes = 0
    for name in sorted(os.listdir(receipts_dir)):
        if not name.lower().endswith(".pdf"):
            continue
        if name in known:
            continue
        if name in dup_registry:
            continue  # подтверждённый дубль уже учтённого чека — не трогаем
        parsed = parse_receipt_pdf(os.path.join(receipts_dir, name))
        if not parsed:
            # позиий не увидели, но текстовый слой может держать реквизиты (ФД/ФПД/ФН, итог):
            # заводим чек-каркас — позиции дотянет API-детализация конвейера
            try:
                import pymupdf as _fitz
                _doc = _fitz.open(os.path.join(receipts_dir, name))
                _text = "".join(page.get_text() for page in _doc)
                _doc.close()
            except Exception:
                _text = ""
            fiscal, total = fiscal_from_text(_text) if _text else ({}, None)
            if not fiscal.get("fn"):
                print(f"{name}: нет текстового слоя или позиций — разбирать вручную")
                continue
            stem = os.path.splitext(name)[0]
            payload["receipts"].append({
                "receipt_id": f"AUTO-PDF-{stem}", "file": name,
                "datetime": _extract_dt_from_text(_text),
                "shop": _extract_shop_from_text(_text), "inn": fiscal.get("inn", ""),
                "total": total, "method": "pdf",
                "note": "Текстовый слой: реквизиты и итог вычитаны; позиции подтянет API-детализация",
                "fiscal": {"fn": fiscal.get("fn", ""), "fd": fiscal.get("fd", ""), "fp": fiscal.get("fp", "")},
            })
            added += 1
            print(f"{name}: каркас чека из реквизитов (итог {total}) — позиции через API")
            continue
        key = fingerprint(parsed)
        if key in known_prints:
            dupes += 1
            dup_rid = next((rid for rid, fp in existing_fingerprints(payload).items() if fp == key), "?")
            dup_registry[name] = dup_rid
            print(f"{name}: повторное вложение уже учтённого чека ({dup_rid}) — пропущен")
            continue
        if name in dup_registry:
            dup_registry.pop(name)
        stem = os.path.splitext(name)[0]
        rid = f"AUTO-PDF-{stem}"
        payload["receipts"].append({
            "receipt_id": rid, "file": name, "datetime": parsed["datetime"],
            "shop": parsed["shop"], "inn": parsed["inn"], "total": parsed["total"],
            "method": "pdf", "note": "Позиции вычитаны из текстового слоя PDF автоматически",
            "fiscal": {},
        })
        for it in parsed["items"]:
            payload["items"].append({"receipt_id": rid, "name": it["name"],
                                     "qty": it["qty"], "price": it["price"], "sum": it["sum"]})
        added += 1
        print(f"{name}: {len(parsed['items'])} позиций, итог {parsed['total']}")
    if added or dupes or dup_registry != _orig_registry:
        with open(DUPLICATES, "w", encoding="utf-8") as fh:
            json.dump(dup_registry, fh, ensure_ascii=False, indent=1)
    if added:
        with open(POSITIONS, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"PDF-чеков добавлено: {added}")


if __name__ == "__main__":
    run_auto()