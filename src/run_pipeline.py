# -*- coding: utf-8 -*-
"""Конвейер обработки чеков одним прогоном.

Шаги:
  1. подготовка изображений и чтение QR-кодов        (prepare_receipts.py)
  2. дополнение data/positions.json сведениями из QR (сумма, дата, ФН/ФД/ФП)
  3. пересборка Excel-отчёта                         (build_report.py)

Позиции внутри чека распознаются отдельным шагом (визуально/через OCR) и
дописываются в data/positions.json. Пока позиции не заполнены, чек всё равно
попадёт в отчёт с признаком расхождения — его видно на листе «Чеки».
"""

import json
import os
import subprocess
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Корень ДАННЫХ: у бота с группами доступа он своя на группу (env DATA_ROOT, см. config/access.json);
# без групп (CLI-запуск) — корень проекта, как раньше.
ROOT = os.environ.get("DATA_ROOT") or CODE_ROOT
SRC = os.path.join(CODE_ROOT, "src")
DATA = os.path.join(ROOT, "data")
POSITIONS = os.path.join(DATA, "positions.json")
QR_RAW = os.path.join(DATA, "qr_raw.json")
REPORT = os.path.join(ROOT, "reports", "Отчёт_затрат_2026.xlsx")


def parse_qr_datetime(value):
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            continue
    return ""


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return default


def save_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def step_prepare():
    proc = subprocess.run([sys.executable, os.path.join(SRC, "prepare_receipts.py")],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1"))
    print(proc.stdout.strip() or proc.stderr.strip())
    return proc.returncode == 0


def step_parse_pdfs():
    """Электронные чеки из PDF-вложений: позиции вычитываются напрямую."""
    proc = subprocess.run([sys.executable, os.path.join(SRC, "parse_pdf.py")],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace",
                          env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1"))
    print((proc.stdout or "").strip() or (proc.stderr or "").strip())
    return proc.returncode == 0


def step_fetch_detail():
    """Детализация позиций чеков с фискальными признаками через API проверки чеков."""
    proc = subprocess.run([sys.executable, os.path.join(SRC, "fetch_detail.py")],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace",
                          env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1"))
    print((proc.stdout or "").strip() or (proc.stderr or "").strip())
    return proc.returncode == 0


def step_update_positions():
    """Добавляет в positions.json чеки, найденные в qr_raw.json, если их там ещё нет."""
    payload = load_json(POSITIONS, {"receipts": [], "items": []})
    payload.setdefault("receipts", [])
    payload.setdefault("items", [])

    qr_entries = load_json(QR_RAW, [])
    known_files = {r.get("file") for r in payload["receipts"]}
    added = 0
    for entry in qr_entries:
        qr = entry.get("qr")
        if not qr or not entry.get("file") or entry["file"] in known_files:
            continue
        fields = qr.get("fields", {})
        stem = os.path.splitext(entry["file"])[0]
        total = fields.get("total") or fields.get("total_alt")
        payload["receipts"].append({
            "receipt_id": f"AUTO-{stem}",
            "file": entry["file"],
            "datetime": parse_qr_datetime(fields.get("datetime", "")),
            "shop": "(магазин не распознан)",
            "inn": "",
            "total": float(total) if total else None,
            "method": "qr",
            "note": "Позиции ещё не распознаны — дополнить вручную или через OCR",
            "fiscal": {"fn": fields.get("fn", ""), "fd": fields.get("fd", ""), "fp": fields.get("fp", "")},
        })
        added += 1
    # файл позиции должен существовать в группе ВСЕГДА — даже если чеков (пока) нет
    if added or not os.path.exists(POSITIONS):
        save_json(POSITIONS, payload)
    print(f"Новых чеков добавлено в positions.json: {added}")
    return payload


def step_self_test():
    """Самопроверка и автоустранение ошибок до сборки отчёта; отчёты о несоответствиях."""
    proc = subprocess.run([sys.executable, os.path.join(SRC, "self_test.py")],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1"))
    print((proc.stdout or "").strip() or (proc.stderr or "").strip())
    return proc.returncode == 0


def step_build():
    # Стратегия: в папке постоянно свежий CSV (не блокируется Excel);
    # XLSX формируется и присылается по /report (build_report.py без --csv-only).
    proc = subprocess.run([sys.executable, os.path.join(SRC, "build_report.py"),
                           "--input", POSITIONS, "--output", REPORT, "--csv-only"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1"))
    print((proc.stdout or "").strip() or (proc.stderr or "").strip())
    if proc.returncode != 0:
        print(f"ВНИМАНИЕ: CSV-отчёт НЕ пересобран (код {proc.returncode}) — см. лог конвейера")
    dash = subprocess.run([sys.executable, os.path.join(SRC, "build_dashboard.py")],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1"))
    print((dash.stdout or "").strip() or (dash.stderr or "").strip())
    return proc.returncode == 0


def main():
    print(f"=== Конвейер чеков: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')} ===")
    ok = step_prepare()
    if ok:
        # порядок важен: QR-чека должен попасть в positions.json ДО запроса к API
        step_update_positions()
        step_parse_pdfs()
        step_fetch_detail()
        step_self_test()  # ошибки устраняются ДО формирования отчёта
        step_build()
    print("=== Готово ===")


if __name__ == "__main__":
    main()
