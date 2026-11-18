# -*- coding: utf-8 -*-
"""Сборка Excel-отчёта о затратах по статьям бюджета.

Вход:  JSON с чеками и позициями (см. README.md, раздел «Формат данных»).
Выход: XLSX с листами:
   1. Сводка           — суммы и даты по статьям бюджета
   2. Позиции          — детализация по каждой позиции чека
   3. Чеки             — сверка итогов чеков
   4. Требует разметки — позиции без статьи
   5. Справочник статей

Запуск:
   python src/build_report.py --input data/positions.json --output reports/Отчёт.xlsx
   python src/build_report.py --demo        # проверка на демо-данных
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

try:  # корректный вывод кириллицы в консоль Windows
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Корень ДАННЫХ: у бота с группами доступа он своя на группу (env DATA_ROOT, см. config/access.json);
# без групп (CLI-запуск) — корень проекта, как раньше.
ROOT = os.environ.get("DATA_ROOT") or CODE_ROOT
DEFAULT_CATEGORIES = os.path.join(CODE_ROOT, "config", "categories.json")
SENDERS_MAP = os.path.join(ROOT, "data", "senders_map.json")


def load_senders_map():
    """Кто прислал файл (подпись отправителя Telegram), filename -> подпись."""
    if not os.path.exists(SENDERS_MAP):
        return {}
    try:
        with open(SENDERS_MAP, encoding="utf-8") as fh:
            return {os.path.basename(k): v for k, v in json.load(fh).items()}
    except Exception:
        return {}

HEAD_FILL = PatternFill("solid", fgColor="1F3864")
TITLE_FONT = Font(bold=True, size=14, color="1F3864")
META_FONT = Font(size=10, italic=True, color="44546A")
HEAD_FONT = Font(bold=True, color="FFFFFF", size=11)
TOTAL_FILL = PatternFill("solid", fgColor="D9E1F2")
WARN_FILL = PatternFill("solid", fgColor="FCE4D6")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
MONEY = '#,##0.00" \u20bd"'
PCT = "0.0%"
DATE_FMT = "DD.MM.YYYY"
DATETIME_FMT = "DD.MM.YYYY HH:MM"

DATE_PATTERNS = [
    ("%Y%m%dT%H%M", r"\d{8}T\d{4}"),
    ("%Y%m%dT%H%M%S", r"\d{8}T\d{6}"),
    ("%Y-%m-%dT%H:%M:%S", r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}"),
    ("%Y-%m-%dT%H:%M", r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}"),
    ("%Y-%m-%d", r"\d{4}-\d{2}-\d{2}"),
    ("%d.%m.%Y %H:%M", r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}"),
    ("%d.%m.%y %H:%M", r"\d{2}\.\d{2}\.\d{2} \d{2}:\d{2}"),
    ("%d.%m.%Y", r"\d{2}\.\d{2}\.\d{4}"),
    ("%d.%m.%y", r"\d{2}\.\d{2}\.\d{2}"),
]


def norm(text):
    return (text or "").lower().replace("\u0451", "\u0435").strip()


def parse_dt(value):
    """Разбирает дату в любом встречающемся формате. Возвращает datetime или None."""
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt, pattern in DATE_PATTERNS:
        if re.fullmatch(pattern, text):
            try:
                candidate = text.replace(" ", "T") if "T" in fmt else text
                return datetime.strptime(candidate, fmt)
            except ValueError:
                return None
    return None


def iso_dt(value):
    parsed = parse_dt(value)
    return parsed.strftime("%Y-%m-%d %H:%M") if parsed else str(value or "").strip()


def load_categories(path=DEFAULT_CATEGORIES):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data["categories"], data.get("unclassified", {"code": "UNCLS", "name": "Требует разметки"})


def classify(name, categories):
    """Возвращает (код статьи, ключевое слово).

    Совпадение ищется по началу слова, поэтому «рис» не сработает внутри «ирис».
    Побеждает самое длинное совпадение.
    """
    target = norm(name)
    best_code, best_kw = None, ""
    for cat in categories:
        for kw in cat["keywords"]:
            needle = norm(kw)
            if not needle:
                continue
            if re.search(r"(?<![\u0430-\u044f\u0451a-z0-9])" + re.escape(needle), target):
                if len(needle) > len(best_kw):
                    best_code, best_kw = cat["code"], needle
    return best_code, best_kw


def enrich(payload, categories, unclassified):
    """Проставляет статьи/магазины позициям и считает контрольные суммы."""
    index = {c["code"]: c for c in categories}
    index[unclassified["code"]] = {"code": unclassified["code"], "name": unclassified["name"]}

    senders = load_senders_map()

    receipts = {}
    for raw in payload.get("receipts", []):
        rec = dict(raw)
        rec["datetime"] = iso_dt(rec.get("datetime"))
        rec["sender"] = rec.get("sender") or senders.get(rec.get("file") or "", "")
        receipts[rec["receipt_id"]] = rec

    items = []
    for raw in payload.get("items", []):
        it = dict(raw)
        code, kw = classify(it.get("name", ""), categories)
        if it.get("category") and it["category"] in index:
            code, kw = it["category"], "оператор"
        if code is None:
            code, kw = unclassified["code"], ""
        it["category"] = code
        it["category_name"] = index[code]["name"]
        it["matched_by"] = kw
        it.setdefault("qty", 1)
        it.setdefault("sum", round(float(it.get("price", 0)) * float(it["qty"]), 2))
        it["sum"] = round(float(it["sum"]), 2)
        rid = it.get("receipt_id")
        rec = receipts.setdefault(rid, {"receipt_id": rid})
        it["shop"] = it.get("shop") or rec.get("shop", "")
        it["inn"] = it.get("inn") or rec.get("inn", "")
        it["sender"] = it.get("sender") or rec.get("sender", "")
        it["datetime"] = iso_dt(it.get("datetime") or rec.get("datetime", ""))
        items.append(it)

    # сумма распознанных позиций по каждому чеку
    sums = defaultdict(float)
    for it in items:
        sums[it["receipt_id"]] += it["sum"]
    for rid, rec in receipts.items():
        rec["items_sum"] = round(sums.get(rid, 0.0), 2)
        total = rec.get("total")
        rec["diff"] = round(float(total) - rec["items_sum"], 2) if total not in (None, "") else None
    return receipts, items


def style_header(ws, row=1, ncols=1):
    for col in range(1, ncols + 1):
        cell = ws.cell(row=row, column=col)
        cell.fill = HEAD_FILL
        cell.font = HEAD_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER
    ws.row_dimensions[row].height = 30


def autosize(ws, widths):
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width


def fmt_date_text(dt, fmt="%d.%m.%Y"):
    """Дата как готовый ТЕКСТ — Apple/iOS-вьюеры искажают форматные коды "DD"
    (21.10 = день года 294 → показывали «294.10.2026»); текст искажать нечем."""
    try:
        return dt.strftime(fmt) if dt else ""
    except AttributeError:
        return str(dt) if dt else ""


def fmt_dt_text(value):
    """Дата/время как текст «ДД.ММ.ГГГГ ЧЧ:ММ»."""
    parsed = parse_dt(value)
    if parsed:
        return parsed.strftime("%d.%m.%Y %H:%M")
    return str(value or "")


def fmt_period(dates):
    real = sorted(d for d in dates if d)
    if not real:
        return "—"
    if real[0].date() == real[-1].date():
        return real[0].strftime("%d.%m.%Y")
    return f"{real[0].strftime('%d.%m.%Y')} — {real[-1].strftime('%d.%m.%Y')}"


def build(categories, unclassified, receipts, items, output):
    wb = Workbook()
    codes = [c["code"] for c in categories] + [unclassified["code"]]
    names = {c["code"]: c["name"] for c in categories}
    names[unclassified["code"]] = unclassified["name"]

    all_dates = [parse_dt(it["datetime"]) for it in items]

    # --- Лист 1: Сводка -----------------------------------------------------
    ws = wb.active
    ws.title = "Сводка"

    agg = defaultdict(lambda: {"sum": 0.0, "qty": 0, "receipts": set(), "dates": []})
    for it in items:
        slot = agg[it["category"]]
        slot["sum"] += it["sum"]
        slot["qty"] += 1
        slot["receipts"].add(it["receipt_id"])
        slot["dates"].append(parse_dt(it["datetime"]))

    grand = round(sum(v["sum"] for v in agg.values()), 2)

    ws.append(["Отчёт о затратах по статьям бюджета"])
    ws.cell(row=1, column=1).font = TITLE_FONT
    ws.append([f"Период данных: {fmt_period(all_dates)}    |    "
               f"Отчётов (чеков): {len(receipts)}    |    "
               f"Сформировано: {datetime.now().strftime('%d.%m.%Y %H:%M')}"])
    ws.cell(row=2, column=1).font = META_FONT
    ws.append([])

    headers = ["Статья", "Код", "Сумма", "Доля", "Позиций", "Чеков", "Дата от", "Дата до"]
    header_row = 4
    ws.append(headers)
    style_header(ws, header_row, len(headers))

    used = [c for c in codes if c in agg]
    for code in sorted(used, key=lambda c: (-agg[c]["sum"], c)):
        slot = agg[code]
        dates = sorted(d for d in slot["dates"] if d)
        ws.append([names[code], code, round(slot["sum"], 2),
                   (slot["sum"] / grand) if grand else 0,
                   slot["qty"], len(slot["receipts"]),
                   fmt_date_text(dates[0]) if dates else "",
                   fmt_date_text(dates[-1]) if dates else ""])

    real_dates = sorted(d for d in all_dates if d)
    ws.append(["ИТОГО", "", grand, 1 if grand else 0, len(items), len(receipts),
               fmt_date_text(real_dates[0]) if real_dates else "",
               fmt_date_text(real_dates[-1]) if real_dates else ""])

    for row in range(header_row + 1, ws.max_row + 1):
        ws.cell(row=row, column=3).number_format = MONEY
        ws.cell(row=row, column=4).number_format = PCT
        ws.cell(row=row, column=7).number_format = DATE_FMT
        ws.cell(row=row, column=8).number_format = DATE_FMT
        for col in range(1, len(headers) + 1):
            ws.cell(row=row, column=col).border = BORDER
        if ws.cell(row=row, column=2).value == unclassified["code"]:
            for col in range(1, len(headers) + 1):
                ws.cell(row=row, column=col).fill = WARN_FILL
    for col in range(1, len(headers) + 1):
        ws.cell(row=ws.max_row, column=col).font = Font(bold=True)
        ws.cell(row=ws.max_row, column=col).fill = TOTAL_FILL
    autosize(ws, [42, 10, 16, 10, 10, 9, 12, 12])
    ws.freeze_panes = f"A{header_row + 1}"

    # --- Лист 1: разбивка по календарным месяцам ----------------------------
    months = defaultdict(lambda: {"sum": 0.0, "qty": 0, "receipts": set()})
    undated_sum = 0.0
    for it in items:
        dt = parse_dt(it["datetime"])
        if not dt:
            undated_sum += it["sum"]
            continue
        slot = months[dt.strftime("%m.%Y")]
        slot["sum"] += it["sum"]
        slot["qty"] += 1
        slot["receipts"].add(it["receipt_id"])

    month_row = ws.max_row + 2
    ws.cell(row=month_row, column=1, value="По календарным месяцам")
    ws.cell(row=month_row, column=1).font = Font(bold=True, size=12, color="1F3864")
    month_title_row = month_row + 1
    month_headers = ["Месяц", "", "Сумма", "Доля", "Позиций", "Чеков"]
    for col, value in enumerate(month_headers, start=1):
        ws.cell(row=month_title_row, column=col, value=value)
    style_header(ws, month_title_row, len(month_headers))

    ordered_months = sorted(months, key=lambda k: tuple(int(p) for p in reversed(k.split("."))))
    for key in ordered_months:
        slot = months[key]
        ws.append([key, "", round(slot["sum"], 2),
                   (slot["sum"] / grand) if grand else 0,
                   slot["qty"], len(slot["receipts"])])
    if undated_sum > 0.004:  # позиции без разобранной даты
        ws.append(["Без даты", "", round(undated_sum, 2),
                   (undated_sum / grand) if grand else 0,
                   "", ""])

    ws.append(["ИТОГО", "", grand, 1 if grand else 0, len(items), len(receipts)])
    for row in range(month_title_row + 1, ws.max_row + 1):
        ws.cell(row=row, column=3).number_format = MONEY
        ws.cell(row=row, column=4).number_format = PCT
        for col in range(1, len(month_headers) + 1):
            ws.cell(row=row, column=col).border = BORDER
    for col in range(1, len(month_headers) + 1):
        ws.cell(row=ws.max_row, column=col).font = Font(bold=True)
        ws.cell(row=ws.max_row, column=col).fill = TOTAL_FILL

    # --- Лист 1: подитоги по календарным неделям (ISO, пн–вс) -----------------
    weeks = defaultdict(lambda: {"sum": 0.0, "qty": 0, "receipts": set(), "dates": []})
    undated_week_sum = 0.0
    for it in items:
        dt = parse_dt(it["datetime"])
        if not dt:
            undated_week_sum += it["sum"]
            continue
        iso = dt.isocalendar()
        key = f"{iso[0]}-W{iso[1]:02d}"
        slot = weeks[key]
        slot["sum"] += it["sum"]
        slot["qty"] += 1
        slot["receipts"].add(it["receipt_id"])
        slot["dates"].append(dt)

    if weeks:
        week_row = ws.max_row + 2
        ws.cell(row=week_row, column=1, value="Подитог за календарные недели (ISO, пн–вс)")
        ws.cell(row=week_row, column=1).font = Font(bold=True, size=12, color="1F3864")
        week_title_row = week_row + 1
        week_headers = ["Неделя", "", "Сумма", "Доля", "Позиций", "Чеков", "С", "по"]
        for col, value in enumerate(week_headers, start=1):
            ws.cell(row=week_title_row, column=col, value=value)
        style_header(ws, week_title_row, len(week_headers))

        ordered_weeks = [k for k, s in sorted(weeks.items(), key=lambda kv: (kv[0][:4], int(kv[0].split("W")[1])))]
        for key in ordered_weeks:
            slot = weeks[key]
            dates = sorted(slot["dates"])
            ws.append([key, "", round(slot["sum"], 2),
                       (slot["sum"] / grand) if grand else 0,
                       slot["qty"], len(slot["receipts"]),
                       fmt_date_text(dates[0], "%d.%m"), fmt_date_text(dates[-1], "%d.%m")])
        if undated_week_sum > 0.004:
            ws.append(["Без даты", "", round(undated_week_sum, 2),
                       (undated_week_sum / grand) if grand else 0, "", "", None, None])
        ws.append(["ИТОГО", "", grand, 1 if grand else 0, len(items), len(receipts), None, None])
        for row in range(week_title_row + 1, ws.max_row + 1):
            ws.cell(row=row, column=3).number_format = MONEY
            ws.cell(row=row, column=4).number_format = PCT
            for col in (7, 8):
                ws.cell(row=row, column=col).number_format = "General"
            for col in range(1, len(week_headers) + 1):
                ws.cell(row=row, column=col).border = BORDER
                ws.cell(row=row, column=col).alignment = Alignment(horizontal="left" if col == 1 else "center",
                                                                   vertical="center")
        for col in range(1, len(week_headers) + 1):
            ws.cell(row=ws.max_row, column=col).font = Font(bold=True)
            ws.cell(row=ws.max_row, column=col).fill = TOTAL_FILL

    # --- Лист 1: матрица «месяц × статья» ------------------------------------
    code_sums = defaultdict(float)
    for it in items:
        code_sums[it["category"]] += it["sum"]
    matrix_used = sorted(code_sums, key=lambda c: (-code_sums[c], c))

    mx = defaultdict(float)
    for it in items:
        dt = parse_dt(it["datetime"])
        key = dt.strftime("%m.%Y") if dt else "Без даты"
        mx[(key, it["category"])] += it["sum"]
    matrix_months = ordered_months + (["Без даты"] if undated_sum > 0.004 else [])

    matrix_title = ws.max_row + 2
    ws.cell(row=matrix_title, column=1, value="Статья × месяц — суммы")
    ws.cell(row=matrix_title, column=1).font = Font(bold=True, size=12, color="1F3864")
    matrix_title_row = matrix_title + 1
    matrix_headers = ["Статья"] + matrix_months + ["Итого"]
    for col, value in enumerate(matrix_headers, start=1):
        ws.cell(row=matrix_title_row, column=col, value=value)
    style_header(ws, matrix_title_row, len(matrix_headers))

    for code in matrix_used:
        row_vals, row_total = [names[code]], 0.0
        for key in matrix_months:
            value = mx.get((key, code))
            row_total += value or 0.0
            row_vals.append(round(value, 2) if value else None)
        row_vals.append(round(row_total, 2))
        ws.append(row_vals)
    ws.append(["ИТОГО"] + [round(sum(mx.get((key, c), 0.0) for c in matrix_used), 2)
                           for key in matrix_months] + [grand])
    for row in range(matrix_title_row + 1, ws.max_row + 1):
        ws.cell(row=row, column=1).border = BORDER
        for col in range(2, len(matrix_headers) + 1):
            cell = ws.cell(row=row, column=col)
            cell.number_format = MONEY
            cell.border = BORDER
    for col in range(1, len(matrix_headers) + 1):
        ws.cell(row=ws.max_row, column=col).font = Font(bold=True)
        ws.cell(row=ws.max_row, column=col).fill = TOTAL_FILL
    for col in range(2, len(matrix_headers) + 1):  # единая ширина колонок матрицы
        ws.column_dimensions[get_column_letter(col)].width = 13

    # --- Лист 2: Позиции ----------------------------------------------------
    ws2 = wb.create_sheet("Позиции")
    head2 = ["№", "Дата/время", "Чек", "Магазин", "ИНН", "Наименование позиции",
             "Кол-во", "Цена", "Сумма", "Статья", "Код", "Определено по", "Комментарий"]
    ws2.append(head2)
    style_header(ws2, 1, len(head2))
    ordered = sorted(items, key=lambda x: (x.get("datetime", ""), x.get("receipt_id", "")))
    for i, it in enumerate(ordered, start=1):
        ws2.append([i, fmt_dt_text(it["datetime"]), it.get("receipt_id", ""),
                    it.get("shop", ""), it.get("inn", ""), it.get("name", ""),
                    it.get("qty", 1), float(it.get("price", 0)), it["sum"],
                    it["category_name"], it["category"], it.get("matched_by", ""),
                    it.get("comment", "")])
    for row in range(2, ws2.max_row + 1):
        ws2.cell(row=row, column=2).number_format = DATETIME_FMT
        ws2.cell(row=row, column=8).number_format = MONEY
        ws2.cell(row=row, column=9).number_format = MONEY
        for col in range(1, len(head2) + 1):
            ws2.cell(row=row, column=col).border = BORDER
        comment = str(ws2.cell(row=row, column=13).value or "").lower()
        needs_check = any(w in comment for w in
                          ("нечитаем", "не читается", "уточнить", "сверить", "проверить"))
        if ws2.cell(row=row, column=11).value == unclassified["code"] or needs_check:
            for col in range(1, len(head2) + 1):
                ws2.cell(row=row, column=col).fill = WARN_FILL
    autosize(ws2, [5, 18, 12, 30, 13, 44, 8, 12, 12, 30, 8, 14, 26])
    ws2.freeze_panes = "A2"
    ws2.auto_filter.ref = f"A1:{get_column_letter(len(head2))}{max(ws2.max_row, 1)}"

    # --- Лист 3: Чеки -------------------------------------------------------
    ws3 = wb.create_sheet("Чеки")
    head3 = ["Чек", "Дата", "Магазин", "ИНН", "Итог в чеке", "Сумма позиций",
             "Расхождение", "Источник", "Принято от", "Позиций", "Примечание"]
    ws3.append(head3)
    style_header(ws3, 1, len(head3))
    per_receipt = defaultdict(int)
    for it in items:
        per_receipt[it["receipt_id"]] += 1
    for rid, rec in sorted(receipts.items(), key=lambda kv: kv[1].get("datetime", "")):
        ws3.append([rid, fmt_dt_text(rec.get("datetime")),
                    rec.get("shop", ""), rec.get("inn", ""), rec.get("total"),
                    rec.get("items_sum", 0), rec.get("diff"), rec.get("method", ""),
                    rec.get("sender", ""), per_receipt.get(rid, 0), rec.get("note", "")])
    for row in range(2, ws3.max_row + 1):
        ws3.cell(row=row, column=2).number_format = DATETIME_FMT
        for col in (5, 6, 7):
            ws3.cell(row=row, column=col).number_format = MONEY
        for col in range(1, len(head3) + 1):
            ws3.cell(row=row, column=col).border = BORDER
        if ws3.cell(row=row, column=7).value not in (None, 0, 0.0):
            ws3.cell(row=row, column=7).fill = WARN_FILL
    autosize(ws3, [12, 18, 30, 13, 14, 14, 14, 14, 18, 10, 44])
    ws3.freeze_panes = "A2"

    # --- Лист 4: Требует разметки ------------------------------------------
    ws4 = wb.create_sheet("Требует разметки")
    head4 = ["№", "Дата", "Чек", "Магазин", "Наименование позиции", "Кол-во", "Цена", "Сумма"]
    ws4.append(head4)
    style_header(ws4, 1, len(head4))
    backlog = [it for it in items if it["category"] == unclassified["code"]]
    for i, it in enumerate(backlog, start=1):
        ws4.append([i, fmt_date_text(parse_dt(it["datetime"])), it.get("receipt_id", ""),
                    it.get("shop", ""), it.get("name", ""), it.get("qty", 1),
                    float(it.get("price", 0)), it["sum"]])
    for row in range(2, ws4.max_row + 1):
        ws4.cell(row=row, column=2).number_format = DATE_FMT
        ws4.cell(row=row, column=7).number_format = MONEY
        ws4.cell(row=row, column=8).number_format = MONEY
    autosize(ws4, [5, 18, 12, 30, 44, 8, 12, 12])
    ws4.freeze_panes = "A2"

    # --- Лист 5: Справочник статей -----------------------------------------
    ws5 = wb.create_sheet("Справочник статей")
    head5 = ["Код", "Статья", "Описание", "Ключевые слова"]
    ws5.append(head5)
    style_header(ws5, 1, len(head5))
    for cat in categories:
        ws5.append([cat["code"], cat["name"], cat.get("description", ""), ", ".join(cat["keywords"])])
    ws5.append([unclassified["code"], unclassified["name"], "Не найдено совпадений — разметить вручную", ""])
    for row in range(2, ws5.max_row + 1):
        for col in range(1, len(head5) + 1):
            ws5.cell(row=row, column=col).border = BORDER
            ws5.cell(row=row, column=col).alignment = Alignment(vertical="top", wrap_text=True)
    autosize(ws5, [8, 34, 38, 90])

    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    wb.save(output)
    try:  # CSV-двойник обновляется той же сборкой, чтобы в папке всегда был свежий снимок
        write_csv(receipts, items, os.path.splitext(output)[0] + ".csv")
    except Exception as exc:
        print(f"CSV-снимок не записан: {exc!r}")
    return {
        "позиций": len(items),
        "чеков": len(receipts),
        "итого": grand,
        "период": fmt_period(all_dates),
        "без_статьи": len(backlog),
        "файл": output,
    }


DEMO = {
    "receipts": [
        {"receipt_id": "R-001", "datetime": "2026-09-03 11:12", "shop": "ООО «Канцлер»",
         "inn": "7701234567", "total": 1560.00, "method": "qr"},
        {"receipt_id": "R-002", "datetime": "2026-09-05 18:40", "shop": "АО «Пятёрочка»",
         "inn": "7709876543", "total": 892.40, "method": "vision"},
    ],
    "items": [
        {"receipt_id": "R-001", "name": "Бумага офисная А4 500 л.", "qty": 2, "price": 380.00, "sum": 760.00},
        {"receipt_id": "R-001", "name": "Ручка шариковая синяя", "qty": 10, "price": 35.00, "sum": 350.00},
        {"receipt_id": "R-001", "name": "Картридж лазерный HP 106A", "qty": 1, "price": 450.00, "sum": 450.00},
        {"receipt_id": "R-002", "name": "Кофе молотый 250 г", "qty": 2, "price": 219.90, "sum": 439.80},
        {"receipt_id": "R-002", "name": "Салфетки бумажные 100 шт", "qty": 3, "price": 49.20, "sum": 147.60},
        {"receipt_id": "R-002", "name": "Батарейки AA 4 шт", "qty": 1, "price": 305.00, "sum": 305.00},
    ],
}


def write_csv(receipts, items, path):
    """CSV-снимок позиций (utf-8 BOM, разделитель ';' — Excel открывает напрямую)."""
    import csv
    ordered = sorted(items, key=lambda x: (x.get("datetime", ""), x.get("receipt_id", "")))
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["№", "Дата/время", "Неделя (ISO)", "Чек", "Магазин", "ИНН",
                         "Наименование", "Кол-во", "Цена", "Сумма",
                         "Статья", "Код", "Принято от", "Комментарий"])
        for i, it in enumerate(ordered, start=1):
            dt = parse_dt(it.get("datetime"))  # noqa: F821 — parse_dt в этом модуле
            week = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}" if dt else ""
            writer.writerow([i, it.get("datetime", ""), week, it.get("receipt_id", ""),
                             it.get("shop", ""), it.get("inn", ""), it.get("name", ""),
                             it.get("qty", 1), float(it.get("price", 0)), it["sum"],
                             it["category_name"], it["category"], it.get("sender", ""), it.get("comment", "")])
    return len(ordered)


def main():
    parser = argparse.ArgumentParser(description="Excel-отчёт по статьям бюджета")
    parser.add_argument("--input", help="JSON с чеками и позициями")
    parser.add_argument("--output", default=os.path.join(ROOT, "reports", "Отчёт_затрат.xlsx"))
    parser.add_argument("--categories", default=DEFAULT_CATEGORIES)
    parser.add_argument("--demo", action="store_true", help="прогон на демо-данных")
    parser.add_argument("--csv-only", action="store_true",
                        help="не собирать XLSX — только CSV-снимок рядом с указанным --output")
    args = parser.parse_args()

    categories, unclassified = load_categories(args.categories)

    if args.csv_only:
        if not args.input:
            parser.error("укажите --input")
        with open(args.input, encoding="utf-8") as fh:
            payload = json.load(fh)
        receipts, items = enrich(payload, categories, unclassified)
        csv_path = os.path.splitext(args.output)[0] + ".csv"
        csv_rows = write_csv(receipts, items, csv_path)
        grand = round(sum(it["sum"] for it in items), 2)
        print(f"CSV: {csv_path} (позиций {csv_rows}, итого {grand} ₽)")
        return

    if args.demo:
        payload = DEMO
        if args.output.endswith("Отчёт_затрат.xlsx"):
            args.output = os.path.join(ROOT, "reports", "Пример_отчёта.xlsx")
    else:
        if not args.input:
            parser.error("укажите --input или --demo")
        with open(args.input, encoding="utf-8") as fh:
            payload = json.load(fh)

    receipts, items = enrich(payload, categories, unclassified)
    result = build(categories, unclassified, receipts, items, args.output)
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
