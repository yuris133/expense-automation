# -*- coding: utf-8 -*-
"""Подготовка чеков: EXIF-разворот, уменьшение, чтение QR.

Результат:
  data/prepared/*.jpg  — выровненные уменьшенные копии для распознавания
  data/qr_raw.json     — фискальные данные, извлечённые из QR-кодов
"""

import json
import os
import sys
from urllib.parse import parse_qs, unquote

from PIL import Image, ImageOps

try:
    import cv2
    import numpy as np
except ImportError:  # QR-декодер необязателен
    cv2 = None
try:
    from pyzbar.pyzbar import decode as zbar_decode
except (ImportError, OSError):
    # Windows: pyzbar тянет libiconv.dll из своей папки, а поиск систем Windows не видит —
    # показываем каталог DLL явно и импортируем повторно
    try:
        import os as _os
        import pathlib as _pathlib
        pkg = _pathlib.Path(__import__("pyzbar").__file__).parent
        _os.add_dll_directory(str(pkg))
        from pyzbar.pyzbar import decode as zbar_decode
    except Exception:
        zbar_decode = None  # второй декодер необязателен (Linux/Oracle ставится из apt)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Корень ДАННЫХ: у бота с группами доступа он своя на группу (env DATA_ROOT, см. config/access.json);
# без групп (CLI-запуск) — корень проекта, как раньше.
ROOT = os.environ.get("DATA_ROOT") or CODE_ROOT
SRC = os.path.join(ROOT, "receipts")
OUT = os.path.join(ROOT, "data", "prepared")
MAX_SIDE = 1800
EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}

QR_FIELDS = {
    "t": "datetime", "s": "total", "fn": "fn", "i": "fd", "fp": "fp",
    "n": "operation", "sum": "total_alt",
}


def parse_qr_text(text):
    """Разбирает сырую строку QR в фискальные поля."""
    fields = {}
    if text.lower().startswith("http"):
        query = parse_qs(text.split("?", 1)[-1])
        for key, values in query.items():
            fields[key] = unquote(values[0])
    else:
        for pair in text.replace("&", ";").split(";"):
            if "=" in pair:
                key, _, value = pair.partition("=")
                fields[key.strip()] = value.strip()
    return fields


def decode_qr(path):
    """Ищет QR-код, пробуя два декодера и несколько вариантов изображения.

    Живучесть: слабое фото/блик/склон — opencv берёт не всё, pyzbar (zbar) добирает;
    каскад вариантов: исходник → зум → автоуровни → бинаризация Оцу → инверсия.
    """
    if cv2 is None:
        return None
    # cv2.imread не работает с кириллицей в пути — читаем через буфер
    try:
        buf = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
    except Exception:
        img = None
    if img is None:
        return None

    variants = [("src", img)]
    variants.append(("zoom1.6", cv2.resize(img, None, fx=1.6, fy=1.6, interpolation=cv2.INTER_CUBIC)))
    variants.append(("zoom2.4", cv2.resize(img, None, fx=2.4, fy=2.4, interpolation=cv2.INTER_CUBIC)))
    auto = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX)
    variants.append(("auto", auto))
    # бинаризация: локальная (адаптивная) — помогает на бликах и тенях
    for block in (35, 81):
        bin_img = cv2.adaptiveThreshold(auto, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                        cv2.THRESH_BINARY, block, 11)
        variants.append((f"bin{block}", bin_img))
        variants.append((f"bin{block}-inv", 255 - bin_img))

    detector = cv2.QRCodeDetector()
    for label, candidate in variants:
        text = ""
        try:
            text, _, _ = detector.detectAndDecode(candidate)
        except cv2.error:
            text = ""
        if not text and zbar_decode is not None:
            try:
                found = zbar_decode(candidate)
                text = found[0].data.decode("utf-8", errors="ignore") if found else ""
            except Exception:
                text = ""
        if text:
            parsed = {QR_FIELDS.get(k, k): v for k, v in parse_qr_text(text).items()}
            return {"raw": text, "fields": parsed, "decoder": label}

    # запасной приём: cv2 находит УГОЛЫ QR на разных вариантах, кропим область
    # с запасом и усиливаем ×4 — мелкий/мятый QR на полном кадре часто не берётся
    for label, candidate in variants[:5]:
        try:
            points = detector.detect(candidate)
        except cv2.error:
            points = None
        ok = True
        if isinstance(points, tuple):  # новые версии opencv: (found: bool, points)
            ok = bool(points[0])
            points = points[1] if len(points) > 1 else None
        if not ok or points is None:
            continue
        pts = np.asarray(points, dtype=float).reshape(-1, 2)
        x0, y0 = np.floor(pts.min(axis=0)).astype(int)
        x1, y1 = np.ceil(pts.max(axis=0)).astype(int)
        pad = max(20, (x1 - x0 + y1 - y0) // 6)
        x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
        x1, y1 = min(candidate.shape[1], x1 + pad), min(candidate.shape[0], y1 + pad)
        if x1 - x0 < 40 or y1 - y0 < 40:
            continue
        crop = candidate[max(0, y0):y1, max(0, x0):x1]
        scale = max(4, 800 // max(1, crop.shape[1]))
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        text = ""
        try:
            text, _, _ = detector.detectAndDecode(crop)
        except cv2.error:
            text = ""
        if not text and zbar_decode is not None:
            try:
                found = zbar_decode(crop)
                text = found[0].data.decode("utf-8", errors="ignore") if found else ""
            except Exception:
                text = ""
        if text:
            parsed = {QR_FIELDS.get(k, k): v for k, v in parse_qr_text(text).items()}
            return {"raw": text, "fields": parsed, "decoder": f"crop-{label}x{scale}"}
    return None


def prepare(path):
    name = os.path.splitext(os.path.basename(path))[0] + ".jpg"
    target = os.path.join(OUT, name)
    # уже подготовлено и не устарело — не тратим время
    if os.path.exists(target) and os.path.getmtime(target) >= os.path.getmtime(path):
        return target
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode != "RGB":
            im = im.convert("RGB")
        im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
        im.save(target, "JPEG", quality=92)
    return target


def main():
    os.makedirs(OUT, exist_ok=True)
    results = []
    files = sorted(
        f for f in os.listdir(SRC)
        if os.path.splitext(f)[1].lower() in EXTS
    )
    if not files:
        print("В папке receipts/ нет изображений")
        return
    for name in files:
        src = os.path.join(SRC, name)
        prepared = prepare(src)
        # мелкий QR при подготовке (уменьшении) теряется — если копия не читается, пробуем оригинал
        qr = decode_qr(prepared) or decode_qr(src)
        results.append({
            "file": name,
            "prepared": os.path.relpath(prepared, ROOT).replace("\\", "/"),
            "qr": qr,
        })
        status = "QR найден" if qr else "QR не прочитан"
        extra = ""
        if qr:
            amount = qr["fields"].get("total") or qr["fields"].get("total_alt") or "?"
            extra = f" | {qr['fields'].get('datetime', '?')} | {amount} ₽"
        print(f"{name}: {status}{extra}")

    with open(os.path.join(ROOT, "data", "qr_raw.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
    found = sum(1 for r in results if r["qr"])
    print(f"\nВсего файлов: {len(results)}, с QR: {found}")


if __name__ == "__main__":
    main()
