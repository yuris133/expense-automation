# -*- coding: utf-8 -*-
"""Распознавание позиций чека локальным OCR (Tesseract) — без интернета и API-ключей.

Что делает:
  1. готовит несколько вариантов изображения (повороты, автоуровни, бинаризация);
  2. распознаёт каждый вариант и разбирает строки в позиции чека;
  3. выбирает лучший вариант по сходимости суммы позиций с итогом чека;
  4. дописывает позиции в data/positions.json.

Запуск:
   python src/extract_items.py --auto                 # все чеки без позиций
   python src/extract_items.py --image data/prepared/IMG_...jpg --show
"""

import argparse
import json
import os
import re
import shutil
import sys

import numpy as np
import pytesseract
from PIL import Image, ImageOps

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Корень ДАННЫХ: у бота с группами доступа он своя на группу (env DATA_ROOT, см. config/access.json);
# без групп (CLI-запуск) — корень проекта, как раньше.
ROOT = os.environ.get("DATA_ROOT") or CODE_ROOT
DATA = os.path.join(ROOT, "data")
PREPARED = os.path.join(DATA, "prepared")
POSITIONS = os.path.join(DATA, "positions.json")
TESSDATA = os.path.join(CODE_ROOT, "tessdata")
# Tesseract не читает путь с кириллицей, поэтому рабочие модели держим в ASCII-папке
ASCII_TESSDATA = os.path.join(os.environ.get("LOCALAPPDATA", DATA), "ReceiptsOCR", "tessdata")

TESSERACT_CANDIDATES = [
    shutil.which("tesseract"),
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Tesseract-OCR", "tesseract.exe"),
]

MONEY = r"\d[\d\s]{0,12}[.,]\d{2}"
NUM = r"\d+(?:[.,]\d+)?"

# «НАИМЕНОВАНИЕ  1025.00*1  ШТ. =1025.00»
RE_SAME_LINE = re.compile(
    rf"^(?P<name>.+?)\s+(?P<price>{MONEY})\s*[*xх×]\s*(?P<qty>{NUM})\s*"
    rf"(?:шт\.?|кг|л|м)?\.?\s*=?\s*(?P<sum>{MONEY})?\.?\s*$", re.IGNORECASE)
# «1025.00*1 ШТ. =1025.00»
RE_PRICE_QTY = re.compile(
    rf"^(?P<price>{MONEY})\s*[*xх×]\s*(?P<qty>{NUM})\s*(?:шт\.?|кг|л|м)?\.?\s*=?\s*(?P<sum>{MONEY})?\.?\s*$",
    re.IGNORECASE)
# Строка имени, затем «1.000 x 200.00 =200.00»
RE_QTY_X_PRICE = re.compile(
    rf"^(?P<qty>{NUM})\s*[xх×*]\s*(?P<price>{MONEY})\s*=?\s*(?P<sum>{MONEY})?\.?\s*$", re.IGNORECASE)
# Строка имени, затем «199.90   1   199.90»
RE_PRICE_ONLY = re.compile(
    rf"^(?P<price>{MONEY})\s+(?P<qty>{NUM})\s+(?P<sum>{MONEY})\.?\s*$", re.IGNORECASE)
# «СУММА   =929.50» (только сумма без цены и количества)
RE_SUM_ONLY = re.compile(rf"^(?P<sum>{MONEY})\.?\s*$")

RE_TOTAL = re.compile(r"ИТОГ", re.IGNORECASE)
RE_MONEY = re.compile(MONEY)

SHOP_PREFIXES = ("ООО", "ИП ", "ИП\"", "ОАО", "ЗАО", "ПАО", "АО ", "АО\"", "МАГАЗИН")

NOISE_WORDS = (
    "НДС", "СУММА", "ИТОГ", "БЕЗНАЛИЧН", "КАССИР", "СМЕНА", "ФН ", "ФД ", "ФП ", "ФПД",
    "РН ККТ", "ЗН ККТ", "ККТ", "ИНН", "МЕСТО РАСЧЕТ", "СНО", "САЙТ", "ЭЛ. АДР", "ЭЛ АДР",
    "ПРИХОД", "РАСХОД", "ВОЗВРАТ", "ЧЕК", "ЗАКАЗ", "СТОЛ", "СПАСИБО", "ПОЛУЧИТЕ",
    "ТЕЛЕФОН", "АДРЕС", "САЙТ ФНС", "РЕЖИМ", "НАЛОГ", "ПОДАРОК", "ОТСКАНИРОВАВ",
    "ШТРИХ", "КАРТА", "ОПЛАТА", "ЭЛЕКТРОН", "ПРОВЕРЬТЕ", "СУТОЧН", "РУБ",
    "ОБСЛУЖИВАН", "ОТПРАВИТЕЛЯ", "АВТОМАТА", "ПОКУПАТЕЛЯ", "И С ЧАЕВЫМИ", "ЧАЕВЫ",
)

UNIT_WORDS = ("шт", "кг", "л", "м", "уп", "пач")


def setup_tesseract():
    for candidate in TESSERACT_CANDIDATES:
        if candidate and os.path.exists(candidate):
            pytesseract.pytesseract.tesseract_cmd = candidate
            return candidate
    return None


def resolve_tessdata():
    """Возвращает путь к языковым моделям без кириллицы (иначе Tesseract их не видит)."""
    for folder in (os.environ.get("TESSDATA_PREFIX"), ASCII_TESSDATA, TESSDATA):
        if folder and all(ord(ch) < 128 for ch in folder):
            if os.path.isfile(os.path.join(folder, "rus.traineddata")):
                os.environ["TESSDATA_PREFIX"] = folder
                return folder
    # переносим модели из проектной папки в ASCII-путь
    if os.path.isfile(os.path.join(TESSDATA, "rus.traineddata")):
        os.makedirs(ASCII_TESSDATA, exist_ok=True)
        for name in os.listdir(TESSDATA):
            if name.endswith(".traineddata"):
                shutil.copy2(os.path.join(TESSDATA, name), os.path.join(ASCII_TESSDATA, name))
        os.environ["TESSDATA_PREFIX"] = ASCII_TESSDATA
        return ASCII_TESSDATA
    return None


def otsu(image):
    """Бинаризация методом Оцу (numpy, без внешних зависимостей)."""
    arr = np.asarray(image, dtype=np.uint8)
    hist, _ = np.histogram(arr, bins=256, range=(0, 256))
    total = arr.size
    sum_total = float(np.dot(np.arange(256), hist))
    sum_bg = weight_bg = 0.0
    best_var, threshold = 0.0, 127
    for value in range(256):
        weight_bg += hist[value]
        if weight_bg == 0:
            continue
        weight_fg = total - weight_bg
        if weight_fg == 0:
            break
        sum_bg += value * hist[value]
        mean_bg = sum_bg / weight_bg
        mean_fg = (sum_total - sum_bg) / weight_fg
        variance = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if variance > best_var:
            best_var, threshold = variance, value
    return image.point(lambda p: 255 if p > threshold else 0)


def make_variants(path, scale=2):
    variants = []
    with Image.open(path) as im:
        base = ImageOps.exif_transpose(im).convert("L")
    for label, angle in (("r0", 0), ("r90", 90), ("r270", 270)):
        rotated = base.rotate(angle, expand=True) if angle else base
        gray = ImageOps.autocontrast(rotated)
        big = gray.resize((gray.width * scale, gray.height * scale), Image.LANCZOS)
        variants.append((f"{label}-gray", big))
        variants.append((f"{label}-otsu", otsu(big)))
    return variants


def to_money(raw):
    """Приводит распознанное число к float, разбирая дефекты OCR.

    «1.99.90» → 199.90 · «6.700.00» → 6700.00 · «929.5O» → 929.50
    """
    if raw is None:
        return None
    text = str(raw).strip().replace(" ", "").replace(",", ".")
    text = (text.replace("O", "0").replace("o", "0").replace("З", "3")
                .replace("Б", "6").replace("I", "1").replace("l", "1"))
    text = text.strip("=.:;")
    parts = [p for p in text.split(".") if p != ""]
    if not parts:
        return None
    if len(parts) == 1:
        value = parts[0]
    elif len(parts) == 2:
        value = parts[0] + "." + parts[1][:2]
    else:
        mid = parts[1]
        integer = parts[0] + (mid if len(mid) == 3 else "")
        decimals = parts[2] if len(mid) == 3 else mid
        value = integer + "." + decimals[:2]
    try:
        return round(float("".join(ch for ch in value if ch.isdigit() or ch == ".")), 2)
    except ValueError:
        return None


def clean_name(raw):
    name = re.sub(r"\s+", " ", raw or "").strip(" .·*-—–|")
    name = re.sub(r"^(шт\.?|ШТ\.?)\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"^(товар|услуга|работа)\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*(руб\.?|НДС.*)$", "", name, flags=re.IGNORECASE)
    return name.strip(" .·*-—–|")


def is_noise(line):
    upper = line.upper()
    if any(word in upper for word in NOISE_WORDS):
        return True
    if re.fullmatch(r"[\d\s.,:=*xх×%+-]+", line or ""):
        return True
    if re.search(r"\d{2}[.:]\d{2}[.:]\d{2,4}", line):  # даты и время
        return True
    if re.fullmatch(r"[^\w]+", line):
        return True
    return False


def parse_receipt_text(text):
    """Возвращает (позиции, итог чека, контрагент)."""
    items, total, shop = [], None, ""
    pending_name = ""

    for raw_line in text.splitlines():
        line = re.sub(r"[«»\"]", " ", raw_line).strip()
        if not line:
            continue

        if RE_TOTAL.search(line) and "НДС" not in line.upper():
            amounts = [to_money(m) for m in RE_MONEY.findall(line)]
            amounts = [a for a in amounts if a]
            if amounts:
                total = max(amounts) if total is None else max(total, max(amounts))
            continue

        if not shop:
            upper = line.upper()
            if any(upper.startswith(p) for p in SHOP_PREFIXES):
                shop = clean_name(line)[:80]

        match = RE_SAME_LINE.match(line)
        if match:
            qty = to_money(match.group("qty")) or 1
            price = to_money(match.group("price"))
            amount = to_money(match.group("sum")) or (round(price * qty, 2) if price else None)
            name = clean_name(match.group("name"))
            if name and amount is not None and not is_noise(name):
                items.append({"name": name, "qty": qty, "price": price, "sum": amount})
                pending_name = ""
                continue

        match = RE_PRICE_QTY.match(line)
        if match:
            qty = to_money(match.group("qty")) or 1
            price = to_money(match.group("price"))
            amount = to_money(match.group("sum")) or (round(price * qty, 2) if price else None)
            if pending_name and amount is not None:
                items.append({"name": pending_name, "qty": qty, "price": price, "sum": amount})
                pending_name = ""
                continue

        match = RE_QTY_X_PRICE.match(line)
        if match:
            qty = to_money(match.group("qty")) or 1
            price = to_money(match.group("price"))
            amount = to_money(match.group("sum")) or (round(price * qty, 2) if price else None)
            if pending_name and amount is not None:
                items.append({"name": pending_name, "qty": qty, "price": price, "sum": amount})
                pending_name = ""
                continue

        match = RE_PRICE_ONLY.match(line)
        if match:
            price = to_money(match.group("price"))
            qty = to_money(match.group("qty")) or 1
            amount = to_money(match.group("sum")) or price
            if pending_name and amount is not None:
                items.append({"name": pending_name, "qty": qty, "price": price, "sum": amount})
                pending_name = ""
                continue

        if is_noise(line):
            continue

        name = clean_name(line)
        if len(name) >= 3 and re.search(r"[А-Яа-яA-Za-z]", name):
            pending_name = name

    # позиции вида «имя … сумма» без цены и количества
    return items, total, shop


def score(items, total):
    if not items:
        return 0.0
    amount = round(sum(i["sum"] for i in items), 2)
    points = float(len(items))
    if total:
        diff = abs(amount - total)
        if diff < 0.05:
            points += 25
        elif diff < 1:
            points += 8
        else:
            points -= min(diff / max(total, 1) * 20, 10)
    return points


def ocr_receipt(path, tessdata=None, keep_variants=False):
    variants = make_variants(path)
    best = {"score": -1e9, "variant": "", "items": [], "total": None, "shop": "", "text": ""}
    results = []
    for label, image in variants:
        for psm in (6, 4):
            # путь к моделям передаём через TESSDATA_PREFIX — pytesseract сам
            # экранирует аргументы, и кавычки внутри --tessdata-dir ломают путь
            if tessdata:
                os.environ["TESSDATA_PREFIX"] = tessdata
            config = f"--oem 3 --psm {psm}"
            try:
                text = pytesseract.image_to_string(image, lang="rus+eng", config=config)
            except Exception as exc:  # OCR не должен ронять весь прогон
                results.append({"variant": f"{label}/psm{psm}", "score": 0, "items": [],
                                "total": None, "shop": "", "text": "", "error": repr(exc)[:120]})
                continue
            items, total, shop = parse_receipt_text(text)
            points = score(items, total)
            entry = {"variant": f"{label}/psm{psm}", "score": round(points, 2),
                     "items": items, "total": total, "shop": shop, "text": text}
            results.append(entry)
            if points > best["score"]:
                best = entry
            if points >= 25 + len(items):  # идеальное совпадение — дальше искать незачем
                best = entry
                return best, results
    return best, results


def find_prepared_image(receipt):
    stem = os.path.splitext(receipt.get("file", ""))[0]
    if not stem:
        return None
    candidate = os.path.join(PREPARED, stem + ".jpg")
    return candidate if os.path.exists(candidate) else None


def load_positions():
    if os.path.exists(POSITIONS):
        with open(POSITIONS, encoding="utf-8") as fh:
            return json.load(fh)
    return {"receipts": [], "items": []}


def save_positions(payload):
    with open(POSITIONS, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def parse_fiscal(text):
    """Фискальные признаки из текста чека (OCR): ФН/ФД/ФП видны текстом даже когда QR бит."""
    flat = re.sub(r"\s+", " ", text)
    out = {}
    m = re.search(r"ФН[:\s]*\.?:?\s*(\d{12,20})", flat, re.IGNORECASE)
    if m:
        out["fn"] = m.group(1)
    m = re.search(r"ФД[:\s]*\.?:?\s*(\d{1,10})\b", flat, re.IGNORECASE)
    if m:
        out["fd"] = m.group(1)
    m = re.search(r"ФП[Д]?\s*[:.]?\s*(\d{5,15})", flat, re.IGNORECASE)
    if m:
        out["fp"] = m.group(1)
    return out


def apply_receipt(payload, receipt, items, total, shop, fiscal=None):
    rid = receipt["receipt_id"]
    payload["items"] = [it for it in payload["items"] if it.get("receipt_id") != rid]

    detected = round(sum(i["sum"] for i in items), 2)
    diff = round((total or 0) - detected, 2) if total else None
    note_parts = [f"OCR Tesseract, позиций: {len(items)}"]
    if diff is not None:
        note_parts.append(f"сумма позиций {detected:.2f} ₽" + ("" if abs(diff) < 0.05 else f", расхождение с итогом {diff:.2f} ₽"))
    receipt["method"] = "ocr"
    receipt["note"] = "; ".join(note_parts)
    if fiscal:
        cur = receipt.setdefault("fiscal", {})
        for key, value in fiscal.items():
            cur.setdefault(key, value)
        if receipt["fiscal"]:
            receipt["note"] += "; фискальные признаки вычитаны OCR — API-детализация возможна"

    if total and not receipt.get("total"):
        receipt["total"] = total
    if shop and (not receipt.get("shop") or "не распознан" in str(receipt.get("shop"))):
        receipt["shop"] = shop

    clean_items = []
    for item in items:
        entry = {"receipt_id": rid, "name": item["name"], "qty": item["qty"],
                 "price": item["price"] if item["price"] is not None else item["sum"],
                 "sum": item["sum"]}
        if diff is not None and abs(diff) >= 0.05:
            entry["comment"] = "распознано локальным OCR — проверить"
        clean_items.append(entry)
    payload["items"].extend(clean_items)
    return {"items": len(clean_items), "detected": detected, "total": total, "diff": diff}


def run_auto(only_missing=True, tessdata=None):
    payload = load_positions()
    done = []
    for receipt in payload.get("receipts", []):
        rid = receipt.get("receipt_id")
        if only_missing and any(it.get("receipt_id") == rid for it in payload["items"]):
            continue
        image = find_prepared_image(receipt)
        if not image:
            print(f"{rid}: изображение не найдено — пропуск")
            continue
        best, _ = ocr_receipt(image, tessdata=tessdata)
        fiscal = parse_fiscal(best.get("text", ""))
        if not best["items"]:
            if fiscal.get("fn"):
                receipt.setdefault("fiscal", {})
                for k, v in fiscal.items():
                    receipt["fiscal"].setdefault(k, v)
                print(f"{rid}: позиций не распознано, но ФД/ФП вычитаны — детализация догонит через API")
            else:
                print(f"{rid}: позиции не распознаны ({best['variant']})")
            continue
        stats = apply_receipt(payload, receipt, best["items"], best["total"], best["shop"], fiscal=fiscal)
        print(f"{rid}: {stats['items']} позиций, сумма {stats['detected']:.2f} ₽, "
              f"итог {stats['total']}, расхождение {stats['diff']} [{best['variant']}]")
        done.append(rid)
    save_positions(payload)
    print(f"\nОбработано чеков: {len(done)}{': ' + ', '.join(done) if done else ''}")
    return done


def main():
    parser = argparse.ArgumentParser(description="Распознавание позиций чека локальным OCR")
    parser.add_argument("--image", help="файл изображения для распознавания")
    parser.add_argument("--auto", action="store_true", help="обработать чеки без позиций")
    parser.add_argument("--all", action="store_true", help="перераспознать все чеки")
    parser.add_argument("--show", action="store_true", help="показать результат распознавания")
    args = parser.parse_args()

    if not setup_tesseract():
        print("Tesseract не найден. Установите его или укажите путь в TESSERACT_CANDIDATES.")
        sys.exit(1)
    tessdata = resolve_tessdata()
    if not tessdata:
        print("Не найдены языковые модели (rus.traineddata). Положите их в папку tessdata проекта.")
        sys.exit(1)

    if args.image:
        best, results = ocr_receipt(args.image, tessdata=tessdata)
        print(f"Лучший вариант: {best['variant']} (оценка {best['score']})")
        print(f"Итог в чеке: {best['total']}, сумма позиций: {round(sum(i['sum'] for i in best['items']), 2)}")
        if args.show:
            for i, item in enumerate(best["items"], start=1):
                print(f"  {i}. {item['name']} — {item['qty']} × {item['price']} = {item['sum']}")
            if best["shop"]:
                print(f"  Контрагент: {best['shop']}")
            print("\nВарианты:", ", ".join(f"{r['variant']}={r['score']}" for r in results))
        return

    run_auto(only_missing=not args.all)


if __name__ == "__main__":
    main()
