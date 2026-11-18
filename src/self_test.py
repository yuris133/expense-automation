# -*- coding: utf-8 -*-
"""Самопроверка данных перед сборкой отчёта — «устранять ошибки до отчёта».

Проверки (порядок в конвейере: после prepare/QR/PDF/API, до build_report):
  1. Каждому файлу в receipts/ соответствует чек в data/positions.json.
     Пропущенные: PDF — ругается (parse_pdf уже отработал), фото — попытка OCR;
     если OCR не спасает — добавляется чек-заполнитель с пометкой
     «не распознан автоматически» (файл учтён, ничего не «потеряно»).
  2. Чеки с известным итогом сверяются с суммой распознанных позиций.
  3. Позиции с несуществующим receipt_id (сироты) удаляются.
  4. Чеки без позиций и без фискальных признаков помечаются для ручного разбора.

Выход: понятные строки для бота (которые попадут в сообщение пользователю).
"""

import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Корень ДАННЫХ: у бота с группами доступа он своя на группу (env DATA_ROOT, см. config/access.json);
# без групп (CLI-запуск) — корень проекта, как раньше.
ROOT = os.environ.get("DATA_ROOT") or CODE_ROOT
RECEIPTS = os.path.join(ROOT, "receipts")
POSITIONS = os.path.join(ROOT, "data", "positions.json")
DUPLICATES = os.path.join(ROOT, "data", "pdf_duplicates.json")

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".pdf", ".heic"}
HEAD_MARK = "САМОПРОВЕРКА"


def load_positions():
    if not os.path.exists(POSITIONS):   # новая/пустая группа — создаём стартовый файл
        empty = {"receipts": [], "items": []}
        save_positions(empty)
        return empty
    with open(POSITIONS, encoding="utf-8") as fh:
        return json.load(fh)


def save_positions(p):
    with open(POSITIONS, "w", encoding="utf-8") as fh:
        json.dump(p, fh, ensure_ascii=False, indent=2)


def parse_fiscal_image(ei, best, rec):
    """Вычитывает ФН/ФД/ФП из OCR-текста; True, если нашлись (чек догонит API-детализация)."""
    try:
        fiscal = ei.parse_fiscal(best.get("text", ""))
    except AttributeError:
        return False
    if not fiscal.get("fn"):
        return False
    cur = rec.setdefault("fiscal", {})
    for k, v in fiscal.items():
        cur.setdefault(k, v)
    rec["total"] = rec.get("total") or best.get("total")
    rec["method"] = "ocr-fiscal"
    return True


def try_ocr_add(payload, rec, image_path):
    """Пытается распознать позиции с картинки; True, если добавились."""
    import extract_items as ei
    tess = ei.setup_tesseract()
    tessdata = ei.resolve_tessdata() if tess else None
    if not tess or not tessdata:
        return False
    best, _ = ei.ocr_receipt(image_path, tessdata=tessdata)
    if not best["items"] and not parse_fiscal_image(ei, best, rec):
        return False
    rid = rec["receipt_id"]
    if best["items"]:
        payload["items"] = [it for it in payload["items"] if it.get("receipt_id") != rid]
    detected = round(sum(i["sum"] for i in best["items"]), 2) if best["items"] else 0.0
    rec["method"] = "ocr"
    if best["items"]:
        rec["note"] = f"Позиции распознаны OCR автоматически (Tesseract), поз. {len(best['items'])}"
    else:
        rec["note"] = "Позиций OCR не нашёл, но фискальные признаки вычитаны — ждёт API-детализации"
    if best["total"] and rec.get("total") in (None, ""):
        rec["total"] = best["total"]
    if best["shop"] and (not rec.get("shop") or "не распознан" in str(rec.get("shop"))):
        rec["shop"] = best["shop"]
    diff = round((rec.get("total") or 0) - detected, 2) if rec.get("total") else None
    for it in best["items"]:
        payload["items"].append({
            "receipt_id": rid, "name": it["name"], "qty": it["qty"],
            "price": it["price"] if it["price"] is not None else it["sum"],
            "sum": it["sum"],
            **({"comment": "распознано локальным OCR — проверить"} if diff is not None and abs(diff) >= 0.05 else {}),
        })
    return True


def run():
    p = load_positions()
    pdf_dups = {}
    if os.path.exists(DUPLICATES):
        with open(DUPLICATES, encoding="utf-8") as fh:
            pdf_dups = json.load(fh)
    files = sorted(f for f in os.listdir(RECEIPTS)
                   if os.path.splitext(f)[1].lower() in EXTS and os.path.isfile(os.path.join(RECEIPTS, f))
                   and f not in pdf_dups)  # файлы-дубли учтённого чека — не «потеря»
    by_file = {}
    for rec in p.get("receipts", []):
        f = rec.get("file")
        if f:
            by_file.setdefault(f.lower(), []).append(rec)

    changed = False
    unaccounted = []
    for name in files:
        matches = by_file.get(name.lower())
        if matches:
            continue
        stem = os.path.splitext(name)[0]
        rec = {
            "receipt_id": f"AUTO-MISS-{stem}",
            "file": name,
            "datetime": "",
            "shop": "(не распознан)",
            "inn": "",
            "total": None,
            "method": "unrecognized",
            "note": "Файл был в receipts/, но чека не было — самопроверка; требуется ручной разбор позиций",
            "fiscal": {},
        }
        if name.lower().endswith(".pdf"):
            rec["note"] = "PDF без текстового слоя или без позиций — требуется ручной разбор"
        else:
            prepared = os.path.join(ROOT, "data", "prepared", stem + ".jpg")
            if os.path.exists(prepared) and try_ocr_add(p, rec, prepared):
                rec["note"] = "Позиции распознаны OCR в самопроверке"
        p.setdefault("receipts", []).append(rec)
        unaccounted.append(name)
        changed = True

    # сироты-позиции
    p.setdefault("items", [])
    ids = {r["receipt_id"] for r in p["receipts"]}
    orphans = [it for it in p["items"] if it.get("receipt_id") not in ids]
    if orphans:
        p["items"] = [it for it in p["items"] if it.get("receipt_id") in ids]
        changed = True
        print(f"{HEAD_MARK}: удалено позиций-сирот без чека: {len(orphans)}")

    # сверка сумм
    sums = {}
    for it in p["items"]:
        sums[it["receipt_id"]] = round(sums.get(it["receipt_id"], 0.0) + it.get("sum", 0), 2)
    mismatches = []
    for rec in p["receipts"]:
        total = rec.get("total")
        if total not in (None, ""):
            diff = round(float(total) - sums.get(rec["receipt_id"], 0.0), 2)
            if abs(diff) >= 0.05 and not any(it.get("receipt_id") == rec["receipt_id"] and it.get("comment")
                                             for it in p["items"]):
                mismatches.append(f"{rec['receipt_id']} ({rec.get('shop', '')[:30]}): diff {diff}")
        if not any(it.get("receipt_id") == rec["receipt_id"] for it in p["items"]) \
                and (rec.get("fiscal") or {}).get("fn"):
            print(f"{HEAD_MARK}: чек {rec['receipt_id']} без позиций, детализация API не удалась — "
                  "останется с расхождением до ручного разбора")

    if changed:
        save_positions(p)

    # итоговая сводка для бота/лога
    counts = f"файлов {len(files)}, чеков {len(p['receipts'])}, позиций {len(p['items'])}"
    if unaccounted:
        print(f"{HEAD_MARK}: файлам не соответствует чек — добавлены заполнители: {', '.join(unaccounted)}")
    if mismatches:
        print(f"{HEAD_MARK}: расхождения итога и позиций: {', '.join(mismatches)}")
    print(f"{HEAD_MARK}: {counts} — ок")
    return unaccounted


if __name__ == "__main__":
    run()