# -*- coding: utf-8 -*-
"""Генератор HTML-дашборда затрат по чекам (тренды за всё время).

Читает data/positions.json + config/categories.json и создаёт самодостаточный
reports/Дашборд.html (без внешних библиотек, светлая/тёмная тема, фильтры
периода, всплывающие подсказки, табличный вид).

Запуск: python src/build_dashboard.py [--input data/positions.json]
"""

import argparse
import datetime as dtmod
import json
import os
import re
import sys
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Корень ДАННЫХ: у бота с группами доступа он своя на группу (env DATA_ROOT, см. config/access.json);
# без групп (CLI-запуск) — корень проекта, как раньше.
ROOT = os.environ.get("DATA_ROOT") or CODE_ROOT
POSITIONS = os.path.join(ROOT, "data", "positions.json")
CATEGORIES = os.path.join(CODE_ROOT, "config", "categories.json")
OUTPUT = os.path.join(ROOT, "reports", "Дашборд.html")

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
    return (text or "").lower().replace("ё", "е").strip()


def parse_dt(value):
    if isinstance(value, dtmod.datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt, pattern in DATE_PATTERNS:
        if re.fullmatch(pattern, text):
            try:
                return dtmod.datetime.strptime(text.replace(" ", "T") if "T" in fmt else text, fmt)
            except ValueError:
                return None
    return None


def load_categories(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    cats = data["categories"]
    uncls = data.get("unclassified", {"code": "UNCLS", "name": "Требует разметки"})
    return cats, uncls


def classify(name, categories):
    target = norm(name)
    best_code, best_kw = None, ""
    for cat in categories:
        for kw in cat["keywords"]:
            needle = norm(kw)
            if not needle:
                continue
            if re.search(r"(?<![а-яёa-z0-9])" + re.escape(needle), target):
                if len(needle) > len(best_kw):
                    best_code, best_kw = cat["code"], needle
    return best_code


def build_data(input_path):
    cats, uncls = load_categories(CATEGORIES)
    names = {c["code"]: c["name"] for c in cats}
    names[uncls["code"]] = uncls["name"]

    if not os.path.exists(input_path):  # пустая группа — дашборд в режиме «нет данных»
        payload = {"receipts": [], "items": []}
    else:
        with open(input_path, encoding="utf-8") as fh:
            payload = json.load(fh)

    receipts = {}
    for rec in payload.get("receipts", []):
        parsed = parse_dt(rec.get("datetime"))
        receipts[rec["receipt_id"]] = {
            "datetime": parsed.strftime("%Y-%m-%d %H:%M") if parsed else "",
            "shop": rec.get("shop", ""),
        }

    items = []
    for raw in payload.get("items", []):
        rec = receipts.get(raw.get("receipt_id"), {})
        code = raw.get("category") if raw.get("category") in names else classify(raw.get("name", ""), cats)
        if not code:
            code = uncls["code"]
        parsed = parse_dt(raw.get("datetime") or rec.get("datetime", ""))
        items.append({
            "d": parsed.strftime("%Y-%m-%d") if parsed else None,
            "code": code,
            "name": raw.get("name", ""),
            "sum": round(float(raw.get("sum", 0)), 2),
            "rid": raw.get("receipt_id", ""),
            "shop": raw.get("shop") or rec.get("shop", ""),
        })

    # порядок слотов фиксированный — по порядку справочника среди используемых кодов
    used = [c["code"] for c in cats] + [uncls["code"]]
    used_order = [code for code in used if any(it["code"] == code for it in items)]
    receipts_list = [
        {"d": info["datetime"], "shop": info["shop"],
         "total": round(float(r.get("total")), 2) if r.get("total") not in (None, "") else None}
        for r in payload.get("receipts", []) for rid, info in [(r["receipt_id"], receipts[r["receipt_id"]])]
    ]
    receipts_list.sort(key=lambda x: x["d"], reverse=True)
    max_date = max((it["d"] for it in items if it["d"]), default=dtmod.date.today().isoformat())

    return json.dumps({
        "generated": dtmod.datetime.now().strftime("%d.%m.%Y %H:%M"),
        "maxDate": max_date,
        "order": [{"code": code, "name": names[code]} for code in used_order],
        "items": items,
        "receipts": receipts_list,
    }, ensure_ascii=False)


HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Дашборд затрат по чекам</title>
<style>
:root {
  color-scheme: light;
  --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --ring:rgba(11,11,11,.10);
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100; --s5:#e87ba4;
  --s6:#008300; --s7:#6250d6; --s8:#e34948;
  --wash:rgba(42,120,214,.08);
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page:#0d0d0d; --surface:#1a1a19; --ink:#f0efec; --ink2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,.10);
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --s5:#d55181;
  --s6:#008300; --s7:#9085e9; --s8:#e66767;
  --wash:rgba(57,135,229,.12);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page:#0d0d0d; --surface:#1a1a19; --ink:#f0efec; --ink2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,.10);
    --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --s5:#d55181;
    --s6:#008300; --s7:#9085e9; --s8:#e66767;
    --wash:rgba(57,135,229,.12);
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 24px 16px 48px; background: var(--page); color: var(--ink);
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
}
.wrap { max-width: 1040px; margin: 0 auto; }
header.top { display:flex; align-items:baseline; justify-content:space-between; gap:12px; }
h1 { font-size: 20px; font-weight: 600; margin: 0; }
.meta { color: var(--ink2); font-size: 12px; margin-top: 2px; }
.tbtn {
  font: inherit; font-size: 12px; color: var(--ink2); background: var(--surface);
  border: 1px solid var(--ring); border-radius: 999px; padding: 5px 12px; cursor: pointer;
}
.tbtn:hover { background: var(--wash); }
.filters { display:flex; gap:8px; margin: 18px 0 14px; flex-wrap: wrap; align-items: center; }
.fsep { color: var(--muted); font-size: 12px; margin-left: 14px; }
.fbtn {
  font: inherit; font-size: 13px; color: var(--ink2); background: var(--surface);
  border: 1px solid var(--ring); border-radius: 999px; padding: 6px 14px; cursor: pointer;
}
.fbtn[aria-pressed="true"] { color: var(--ink); background: var(--wash); border-color: transparent; font-weight: 600; }
.fbtn .chk { display:none; font-weight:700; margin-right:4px; }
.fbtn[aria-pressed="true"] .chk { display:inline; }
.grid { display:grid; grid-template-columns: 1fr 1fr; gap: 14px; }
@media (max-width: 800px) { .grid { grid-template-columns: 1fr; } }
.card {
  background: var(--surface); border: 1px solid var(--ring); border-radius: 12px; padding: 16px 18px;
}
.card h2 { font-size: 13px; font-weight: 600; margin: 0 0 2px; }
.card .sub { color: var(--muted); font-size: 11.5px; margin-bottom: 10px; }
.card.wide { grid-column: 1 / -1; }
.kpis { display:grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 14px; }
@media (max-width: 800px) { .kpis { grid-template-columns: repeat(2, 1fr); } }
.tile { background: var(--surface); border: 1px solid var(--ring); border-radius: 12px; padding: 14px 16px; }
.tile .label { color: var(--ink2); font-size: 12px; }
.tile .value { font-size: 26px; font-weight: 600; margin-top: 2px; }
.tile .value.big { font-size: 34px; }
.tile .note { color: var(--muted); font-size: 11.5px; margin-top: 2px; }
svg { display:block; width:100%; height:auto; }
svg text { font-family: inherit; }
/* 9-я и далее сущности выходят за 8 валидных оттенков — им положен нейтральный серый
   (вторым каналом всегда подпись: легенда, label бара). Выдумывать 9-й цвет запрещено. */
.sg { fill: var(--axis); }
.s1{fill:var(--s1)} .s2{fill:var(--s2)} .s3{fill:var(--s3)} .s4{fill:var(--s4)}
.s5{fill:var(--s5)} .s6{fill:var(--s6)} .s7{fill:var(--s7)} .s8{fill:var(--s8)}
.legend { display:flex; flex-wrap:wrap; gap: 6px 18px; margin: 8px 0 4px; }
.legend span { display:inline-flex; align-items:center; gap:7px; color: var(--ink2); font-size: 12px; }
.legend i { width:12px; height:12px; border-radius:3px; display:inline-block; }
table { width:100%; border-collapse: collapse; font-size: 12.5px; }
th { text-align:left; color: var(--ink2); font-weight: 600; border-bottom: 1px solid var(--axis); padding: 6px 8px; }
td { padding: 6px 8px; border-bottom: 1px solid var(--grid); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
td.strong { font-weight: 600; }
#tip {
  position: fixed; z-index: 10; pointer-events: none; display: none;
  background: var(--surface); border: 1px solid var(--ring); border-radius: 10px;
  box-shadow: 0 6px 24px rgba(0,0,0,.14); padding: 9px 12px; min-width: 150px;
}
#tip .t-title { color: var(--muted); font-size: 11px; margin-bottom: 5px; }
#tip .t-row { display:flex; align-items:center; gap:8px; margin-top:3px; }
#tip .t-key { width:14px; height:3px; border-radius:2px; flex:none; }
#tip .t-val { font-weight: 600; font-variant-numeric: tabular-nums; }
#tip .t-name { color: var(--ink2); font-size: 12px; }
.hit { fill: transparent; cursor: pointer; }
.hit:focus { outline: 1px solid var(--axis); outline-offset: -1px; }
.bar-hover .bar { transition: filter .12s; }
.bar-hover td:hover { background: var(--wash); }
footer { color: var(--muted); font-size: 11.5px; margin-top: 18px; }
</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <div>
      <h1>Дашборд затрат по чекам</h1>
      <div class="meta" id="meta"></div>
    </div>
    <button class="tbtn" id="theme" type="button">Тема: авто</button>
  </header>

  <nav class="filters" id="filters" aria-label="Период"></nav>

  <section class="kpis" id="kpis"></section>

  <section class="grid">
    <div class="card">
      <h2 id="h2-months">Расходы по месяцам</h2>
      <div class="sub" id="sub-months">суммы позиций, ₽ · наведите на столбец</div>
      <div id="months"></div>
    </div>
    <div class="card">
      <h2>Статьи бюджета за период</h2>
      <div class="sub">сортировка по сумме · наведите на строку</div>
      <div id="cats"></div>
    </div>
    <div class="card wide">
      <h2>Накопленный итог за всё время</h2>
      <div class="sub">₽ к дате · кроссшер следует за стрелкой</div>
      <div id="cumulative"></div>
    </div>
    <div class="card wide">
      <h2 id="h2-matrix">Статьи × месяцы</h2>
      <div class="sub" id="sub-matrix">столбцы стеком · один tooltip на период</div>
      <div id="legend"></div>
      <div id="stack"></div>
    </div>
    <div class="card wide">
      <h2 id="h2-table">Таблица: статьи × месяцы</h2>
      <div class="sub">полный табличный аналог графиков — без наведения</div>
      <div id="matrix"></div>
    </div>
    <div class="card wide">
      <h2>Чеки за период</h2>
      <div class="sub">новые сверху</div>
      <div id="recv"></div>
    </div>
  </section>

  <footer id="foot"></footer>
</div>
<div id="tip" role="status"></div>

<script>
"use strict";
const DATA = __DATA__;
const SLOTS = 8;
const NF = new Intl.NumberFormat("ru-RU", {minimumFractionDigits: 0, maximumFractionDigits: 2});
const NFD = new Intl.NumberFormat("ru-RU", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const rub = n => NF.format(n) + " ₽";
const rub2 = n => (Math.abs(n) >= 1000 ? NF.format(Math.round(n)) : NFD.format(n)) + " ₽";
const NS = "http://www.w3.org/2000/svg";

const tip = document.getElementById("tip");
function showTip(x, y, title, rows) {
  tip.textContent = "";
  if (title) {
    const t = document.createElement("div");
    t.className = "t-title"; t.textContent = title; tip.appendChild(t);
  }
  for (const r of rows) {
    const row = document.createElement("div"); row.className = "t-row";
    if (r.color) {
      const k = document.createElement("span"); k.className = "t-key";
      k.style.background = r.color; row.appendChild(k);
    }
    const v = document.createElement("span"); v.className = "t-val"; v.textContent = r.value; row.appendChild(v);
    const n = document.createElement("span"); n.className = "t-name"; n.textContent = r.name; row.appendChild(n);
    tip.appendChild(row);
  }
  tip.style.display = "block";
  const w = tip.offsetWidth, h = tip.offsetHeight;
  let px = Math.min(x + 14, window.innerWidth - w - 8);
  let py = y + 14; if (py + h > window.innerHeight - 8) py = y - h - 10;
  tip.style.left = px + "px"; tip.style.top = py + "px";
}
function hideTip() { tip.style.display = "none"; }

function el(name, attrs, text) {
  const node = document.createElementNS(NS, name);
  for (const k in attrs) node.setAttribute(k, attrs[k]);
  if (text != null) node.textContent = text;
  return node;
}

function monthKey(d) { return d ? d.slice(5, 7) + "." + d.slice(0, 4) : "Без даты"; }
function monthSort(k) { if (k === "Без даты") return "0000"; const [m, y] = k.split("."); return y + m; }

function isoWeek(dateStr) {
  const dt = new Date(dateStr + "T00:00:00");
  const d = new Date(Date.UTC(dt.getFullYear(), dt.getMonth(), dt.getDate()));
  const dayNum = d.getUTCDay() || 7;                 // ISO: пн=1 … вс=7
  d.setUTCDate(d.getUTCDate() + 4 - dayNum);         // четверг этой недели
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const week = Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
  return [d.getUTCFullYear(), week];
}
function weekKey(d) { const [y, w] = isoWeek(d); return y + "-W" + String(w).padStart(2, "0"); }
function shortBucketLabel(k) { return k.startsWith("W") ? k : k; }  // недели формируются как "2026-W41"

function aggregate(items) {
  const byMonth = new Map(), byCat = new Map(), cum = [], byMonthCat = new Map();
  const byWeek = new Map(), byWeekCat = new Map();
  let total = 0;
  const sorted = items.filter(it => it.d).slice().sort((a, b) => a.d.localeCompare(b.d) || a.rid.localeCompare(b.rid));
  for (const it of sorted) {
    total += it.sum;
    cum.push({d: it.d, v: Math.round(total * 100) / 100});
    const m = byMonth.get(monthKey(it.d)) || {sum: 0, qty: 0, rec: new Set()};
    m.sum += it.sum; m.qty++; m.rec.add(it.rid); byMonth.set(monthKey(it.d), m);
    const wk = weekKey(it.d);
    const sw = byWeek.get(wk) || {sum: 0, qty: 0, rec: new Set(), start: it.d, end: it.d};
    sw.sum += it.sum; sw.qty++; sw.rec.add(it.rid); sw.end = it.d; byWeek.set(wk, sw);
    const c = byCat.get(it.code) || {sum: 0, qty: 0};
    c.sum += it.sum; c.qty++; byCat.set(it.code, c);
    const mc = byMonthCat.get(monthKey(it.d)) || new Map();
    mc.set(it.code, (mc.get(it.code) || 0) + it.sum); byMonthCat.set(monthKey(it.d), mc);
    const wc = byWeekCat.get(wk) || new Map();
    wc.set(it.code, (wc.get(it.code) || 0) + it.sum); byWeekCat.set(wk, wc);
  }
  const undatedSum = items.filter(it => !it.d).reduce((s, it) => s + it.sum, 0);
  return {byMonth, byCat, byMonthCat, byWeek, byWeekCat, cum, undatedSum, total: items.reduce((s, it) => s + it.sum, 0)};
}

let themeMode = "auto";
// параметр ?theme=light|dark перекрывает кнопку «Тема» — удобно ссылками
themeMode = new URLSearchParams(location.search).get("theme") || "auto";
function applyTheme() {
  const root = document.documentElement;
  if (themeMode === "auto") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", themeMode);
  document.getElementById("theme").textContent =
    "Тема: " + (themeMode === "auto" ? "авто" : themeMode === "dark" ? "тёмная" : "светлая");
}
document.getElementById("theme").addEventListener("click", () => {
  themeMode = themeMode === "auto" ? "dark" : themeMode === "dark" ? "light" : "auto";
  applyTheme(); render();
});

function slotColorClass(idx) {
  return idx < SLOTS ? "s" + (idx + 1) : "sg";   // 9-е: серый (не цвет, а подпись несёт идентификацию)
}

function fmt2(v) { return NFD.format(Math.round(v * 100) / 100); }

// --- столбцы по месяцам ---------------------------------------------------
function renderMonths(mount, months, totals, maxY, activeDays) {
  mount.textContent = "";
  const W = 480, H = 250, padL = 46, padB = 26, padT = 14, padR = 8;
  const svg = el("svg", {viewBox: "0 0 " + W + " " + H, role: "img",
    "aria-label": "Суммы расходов по месяцам"});
  const plotH = H - padB - padT, plotW = W - padL - padR;
  const ticks = [0, Math.round(maxY / 2), maxY].map(v => Math.round(v));
  for (const t of ticks) {
    const y = padT + plotH - (maxY ? plotH * t / maxY : 0);
    svg.appendChild(el("line", {x1: padL, y1: y, x2: W - padR, y2: y, stroke: "var(--grid)", "stroke-width": t ? 1 : 0}));
    svg.appendChild(el("text", {x: padL - 8, y: y + 4, "text-anchor": "end", fill: "var(--muted)", "font-size": 10.5}, t ? NF.format(t) : "0"));
  }
  const band = plotW / months.length;
  const bw = Math.min(24, band * 0.42);
  months.forEach((m, i) => {
    const cx = padL + band * i + band / 2;
    const h = maxY ? plotH * totals[i] / maxY : 0;
    const y = padT + plotH - h;
    const bar = el("path", {class: "bar s1", d:
      "M" + (cx - bw / 2) + "," + (padT + plotH) +
      " v" + (-(h - 4)) + " a4,4 0 0 1 4,-4 h" + (bw - 8) + " a4,4 0 0 1 4,4 v" + (h - 4) + " Z"});
    svg.appendChild(bar);
    svg.appendChild(el("text", {x: cx, y: y - 6, "text-anchor": "middle", fill: "var(--ink2)", "font-size": 11},
      months.length <= 6 ? rub(Math.round(totals[i])) : NF.format(Math.round(totals[i]))));
    svg.appendChild(el("text", {x: cx, y: H - 8, "text-anchor": "middle", fill: "var(--muted)", "font-size": 10.5}, m));
    const hit = el("rect", {class: "hit", x: padL + band * i, y: padT, width: band, height: plotH,
      tabindex: 0, role: "img", "aria-label": m + ": " + rub(totals[i])});
    hit.addEventListener("pointerenter", ev =>
      showTip(ev.clientX, ev.clientY, m + " · " + rub(totals[i]), detailRows(m)));
    hit.addEventListener("focus", () => {
      const r = svg.getBoundingClientRect();
      showTip(r.left + cx * r.width / W, r.top + (y * r.height / H), m + " · " + rub(totals[i]), detailRows(m));
    });
    hit.addEventListener("pointerleave", hideTip);
    hit.addEventListener("blur", hideTip);
    svg.appendChild(hit);
  });
  svg.appendChild(el("line", {x1: padL, y1: padT + plotH, x2: W - padR, y2: padT + plotH, stroke: "var(--axis)", "stroke-width": 1}));
  mount.appendChild(svg);
}

function detailRows(m) {
  const map = aggUnit === "week" ? agg.byWeekCat : agg.byMonthCat;
  const mc = map.get(m);
  if (!mc) return [{value: "", name: "нет данных"}];
  return DATA.order.filter(o => mc.get(o.code))
    .map(o => ({color: cssSlot(o.code), value: rub2(mc.get(o.code)), name: o.name}));
}

// --- статьи: горизонтальные бары -------------------------------------------
function labelLines(name, maxLen) {
  // Длинные имена статей в SVG не обрезаем: переносим на ≤2 строки по границе слова
  const words = name.split(" ");
  const lines = [];
  let current = "";
  for (const word of words) {
    if (current && (current + " " + word).length > maxLen) { lines.push(current); current = word; }
    else current = current ? current + " " + word : word;
    if (lines.length === 2) break;
  }
  if (current && lines.length < 2) lines.push(current);
  else if (current) lines[1] = (lines[1] || "") + (lines[1] ? " " : "") + current;
  return lines.slice(0, 2);
}

function renderCats(mount, catRows) {
  mount.textContent = "";
  const cats = catRows.filter(r => r.sum > 0);
  if (!cats.length) { const p = document.createElement("p"); p.textContent = "Нет данных"; mount.appendChild(p); return; }
  const W = 480, rowH = 32, padL = 190, padR = 60, padT = 6;
  const H = padT + cats.length * rowH + 10;
  const svg = el("svg", {viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Суммы по статьям бюджета"});
  const plotW = W - padL - padR;
  const maxV = cats[0].sum;
  cats.forEach((r, i) => {
    const y = padT + i * rowH, barH = 16;
    const w = Math.max(3, plotW * r.sum / maxV);
    const lines = labelLines(r.name, 26);
    lines.forEach((lineText, li) => {
      svg.appendChild(el("text", {x: padL - 10, y: y + 8 + li * 13, "text-anchor": "end",
        fill: "var(--ink2)", "font-size": 11.5}, lineText));
    });
    svg.appendChild(el("rect", {class: "bar " + slotColorClass(r.idx), x: padL, y: y, width: w, height: barH, rx: 4, ry: 4, clipPath: "inset(0 0 0 0)"}));
    svg.appendChild(el("text", {x: padL + w + 8, y: y + barH - 3, fill: "var(--ink)", "font-size": 11.5}, rub2(r.sum)));
    const hit = el("rect", {class: "hit", x: padL - w - 2, y: y - 4, width: w + padR - 54 + 2,
      height: barH + 8, tabindex: 0, "aria-label": r.name + ": " + rub(r.sum)});
    hit.addEventListener("pointerenter", ev => showTip(ev.clientX, ev.clientY, null,
      [{color: cssSlot(r.code), value: rub2(r.sum), name: r.name + " · " + r.qty + " поз."}]));
    hit.addEventListener("focus", () => { const b = hit.getBoundingClientRect(); showTip(b.right, b.top, null,
      [{color: cssSlot(r.code), value: rub2(r.sum), name: r.name + " · " + r.qty + " поз."}]); });
    hit.addEventListener("pointerleave", hideTip); hit.addEventListener("blur", hideTip);
    svg.appendChild(hit);
  });
  mount.appendChild(svg);
}

// --- накопленный итог -------------------------------------------------------
function renderCumulative(mount, cum) {
  mount.textContent = "";
  if (!cum.length) { const p = document.createElement("p"); p.textContent = "Нет данных"; mount.appendChild(p); return; }
  const W = 1010, H = 240, padL = 56, padB = 30, padT = 14, padR = 56;
  const svg = el("svg", {viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Накопленный итог по датам покупок"});
  const plotH = H - padB - padT, plotW = W - padL - padR;
  const xs = cum.map(p => +new Date(p.d + "T00:00:00"));
  const t0 = Math.min(...xs), t1 = Math.max(...xs);
  const maxY = cum[cum.length - 1].v;
  for (const f of [0, .5, 1]) {
    const y = padT + plotH - plotH * f, val = Math.round(maxY * f);
    svg.appendChild(el("line", {x1: padL, y1: y, x2: W - padR, y2: y, stroke: f ? "var(--grid)" : "none", "stroke-width": 1}));
    svg.appendChild(el("text", {x: padL - 8, y: y + 4, "text-anchor": "end", fill: "var(--muted)", "font-size": 10.5}, f ? NF.format(val) : "0"));
  }
  const X = d => padL + (t1 === t0 ? plotW / 2 : plotW * (d - t0) / (t1 - t0));
  const Y = v => padT + plotH - plotH * v / maxY;
  let area = "M" + X(xs[0]) + "," + (padT + plotH);
  let path = "";
  xs.forEach((x, i) => {
    path += (i ? " L" : "M") + X(x) + "," + Y(cum[i].v);
    area += " L" + X(x) + "," + Y(cum[i].v);
  });
  area += " L" + X(xs[xs.length - 1]) + "," + (padT + plotH) + " Z";
  svg.appendChild(el("path", {d: area, fill: "var(--s1)", opacity: .1}));
  svg.appendChild(el("path", {d: path, fill: "none", stroke: "var(--s1)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round"}));
  const fmtDate = d => { const dt = new Date(d + "T00:00:00"); return dt.toLocaleDateString("ru-RU", {day: "2-digit", month: "2-digit"}); };
  svg.appendChild(el("text", {x: X(t0), y: H - 8, "text-anchor": "middle", fill: "var(--muted)", "font-size": 10.5}, fmtDate(cum[0].d)));
  svg.appendChild(el("text", {x: X(t1), y: H - 8, "text-anchor": "end", fill: "var(--muted)", "font-size": 10.5}, fmtDate(cum[cum.length - 1].d)));
  const cross = el("line", {x1: 0, y1: padT, x2: 0, y2: padT + plotH, stroke: "var(--axis)", "stroke-width": 1, opacity: 0});
  svg.appendChild(cross);
  const dot = el("circle", {cx: 0, cy: 0, r: 5, fill: "var(--s1)", stroke: "var(--surface)", "stroke-width": 2, opacity: 0});
  svg.appendChild(dot);
  svg.appendChild(el("line", {x1: padL, y1: padT + plotH, x2: W - padR, y2: padT + plotH, stroke: "var(--axis)", "stroke-width": 1}));
  svg.appendChild(el("rect", {class: "hit", x: padL, y: padT, width: plotW, height: plotH, tabindex: 0,
    "aria-label": "Накопленный итог: " + rub(maxY)}));
  const overlay = svg.lastChild;
  function onMove(clientX) {
    const bounds = svg.getBoundingClientRect();
    const px = (clientX - bounds.left) / bounds.width * W;
    let best = 0, bd = 1e9;
    xs.forEach((x, i) => { const dd = Math.abs(X(x) - px); if (dd < bd) { bd = dd; best = i; } });
    cross.setAttribute("x1", X(xs[best])); cross.setAttribute("x2", X(xs[best]));
    cross.setAttribute("opacity", 1);
    dot.setAttribute("cx", X(xs[best])); dot.setAttribute("cy", Y(cum[best].v)); dot.setAttribute("opacity", 1);
    showTip(clientX, bounds.top + Math.min(120, bounds.height / 2), "Накоплено к " + fmtDate(cum[best].d),
      [{color: cssSlotVar(0), value: rub(cum[best].v), name: "итог по всем статьям"}]);
  }
  overlay.addEventListener("pointermove", ev => onMove(ev.clientX));
  overlay.addEventListener("pointerleave", () => { hideTip(); cross.setAttribute("opacity", 0); dot.setAttribute("opacity", 0); });
  overlay.addEventListener("focus", () => { const b = overlay.getBoundingClientRect(); onMove(b.right); });
  overlay.addEventListener("blur", () => { hideTip(); cross.setAttribute("opacity", 0); dot.setAttribute("opacity", 0); });
  mount.appendChild(svg);
}

// --- стек по дням ------------------------------------------------------------
function renderStack(mount, months, series) {
  mount.textContent = "";
  const W = 1010, H = 260, padL = 56, padB = 26, padT = 14, padR = 8;
  const svg = el("svg", {viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Расходы по месяцам в разрезе статей"});
  const plotH = H - padB - padT, plotW = W - padL - padR;
  const maxY = Math.max(1, ...series.map(s => s.total));
  for (const f of [0, .5, 1]) {
    const y = padT + plotH - plotH * f, val = Math.round(maxY * f);
    svg.appendChild(el("line", {x1: padL, y1: y, x2: W - padR, y2: y, stroke: f ? "var(--grid)" : "none", "stroke-width": 1}));
    svg.appendChild(el("text", {x: padL - 8, y: y + 4, "text-anchor": "end", fill: "var(--muted)", "font-size": 10.5}, f ? NF.format(val) : "0"));
  }
  const band = plotW / months.length;
  const bw = Math.min(24, band * 0.42);
  months.forEach((m, i) => {
    const cx = padL + band * i + band / 2;
    let yTop = padT + plotH;
    const rows = [];
    for (const s of series) {
      const sv = s.values[i]; if (!sv) continue;
      const h = plotH * sv / maxY;
      const isTop = ys => true;
      rows.push({s: s, h: h, y: yTop - h});
      yTop -= h;
    }
    rows.forEach((r, ri) => {
      const gap = rows.length > 1 ? 1 : 0;
      let yy = r.y + (r.h > 4 ? gap : 0), hh = Math.max(r.h - 2 * (rows.length > 1 ? 1 : 0), 1);
      let cls = "bar " + slotColorClass(r.s.idx);
      const roundedTop = ri === rows.length - 1 && r.h > 8;
      if (roundedTop) {
        const rad = Math.min(4, hh / 2, bw / 2);
        const x0 = cx - bw / 2;
        svg.appendChild(el("path", {class: cls, d: "M" + x0 + "," + (yy + hh) + " v" + (-(hh - rad)) +
          " a" + rad + "," + rad + " 0 0 1 " + rad + ",-" + rad + " h" + (bw - 2 * rad) +
          " a" + rad + "," + rad + " 0 0 1 " + rad + "," + rad + " v" + (hh - rad) + " Z"}));
      } else {
        svg.appendChild(el("rect", {class: cls, x: cx - bw / 2, y: yy, width: bw, height: hh}));
      }
    });
    const isWeek = aggUnit === "week";
    const shortM = isWeek ? m.replace(/^\d{4}-/, "") : m;
    const sw = isWeek ? agg.byWeek.get(m) : null;
    const rangeSuffix = sw ? " (" + sw.start.slice(8, 10) + "." + sw.start.slice(5, 7) + "–" +
        sw.end.slice(8, 10) + "." + sw.end.slice(5, 7) + ")" : "";
    svg.appendChild(el("text", {x: cx, y: H - 8, "text-anchor": "middle", fill: "var(--muted)", "font-size": 10.5}, shortM));
    const total = series.reduce((s, r) => s + (r.values[i] || 0), 0);
    if (total > 0) {
      const hit = el("rect", {class: "hit", x: padL + band * i, y: padT, width: band, height: plotH,
        tabindex: 0, role: "img", "aria-label": m + ": " + rub(total)});
      hit.addEventListener("pointerenter", ev => showTip(ev.clientX, ev.clientY, m + rangeSuffix + " · " + rub(total), detailRows(m)));
      hit.addEventListener("focus", () => { const b = hit.getBoundingClientRect(); showTip(b.left + b.width / 2, b.top,
        m + " · " + rub(total), detailRows(m)); });
      hit.addEventListener("pointerleave", hideTip); hit.addEventListener("blur", hideTip);
      svg.appendChild(hit);
    }
  });
  svg.appendChild(el("line", {x1: padL, y1: padT + plotH, x2: W - padR, y2: padT + plotH, stroke: "var(--axis)", "stroke-width": 1}));
  mount.appendChild(svg);
}

// --- состояние -----------------------------------------------------------------
const SLOT_VARS = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)", "var(--s6)", "var(--s7)", "var(--s8)"];
const slotIndex = new Map();
DATA.order.forEach((o, i) => slotIndex.set(o.code, i));
function cssSlot(code) { return SLOT_VARS[Math.min(slotIndex.get(code) || 0, SLOTS - 1)]; }
function cssSlotVar(i) { return SLOT_VARS[Math.min(i, SLOTS - 1)]; }

let agg = null;
let days = 0;
let aggUnit = "month";  // month | week — разрез периодических графиков
if (new URLSearchParams(location.search).get("agg") === "week") aggUnit = "week";  // ?agg=week — для ссылок/проверки

function slice(items, ndays) {
  if (!ndays) return items.slice();
  const limit = +new Date(DATA.maxDate + "T00:00:00") - ndays * 86400000;
  return items.filter(it => it.d && +new Date(it.d + "T00:00:00") >= limit);
}

function renderLegend(mount) {
  mount.textContent = "";
  const box = document.createElement("div");
  box.className = "legend";
  for (const o of DATA.order) {
    const s = document.createElement("span");
    const i = document.createElement("i"); i.className = slotColorClass(slotIndex.get(o.code)); s.appendChild(i);
    const t = document.createElement("span"); t.textContent = o.name; s.appendChild(t);
    box.appendChild(s);
  }
  mount.appendChild(box);
}

function renderTables(items) {
  const matrix = document.getElementById("matrix");
  matrix.textContent = "";
  const tbl = document.createElement("table");
  const buckets = bucketList();
  const {per, perCat} = bucketsAgg();
  const isWeek = aggUnit === "week";
  const thead = document.createElement("tr");
  const th0 = document.createElement("th"); th0.textContent = isWeek ? "Неделя" : "Месяц"; thead.appendChild(th0);
  for (const o of DATA.order) { const th = document.createElement("th"); th.textContent = o.name; thead.appendChild(th); }
  const thT = document.createElement("th"); thT.className = "num"; thT.textContent = "Сумма"; thead.appendChild(thT);
  const thP = document.createElement("th"); thP.className = "num"; thP.textContent = "Поз."; thead.appendChild(thP);
  tbl.appendChild(thead);
  const sh = d => d.slice(8, 10) + "." + d.slice(5, 7);
  for (const m of buckets) {
    const tr = document.createElement("tr");
    const td0 = document.createElement("td"); td0.textContent = m; tr.appendChild(td0);
    for (const o of DATA.order) {
      const td = document.createElement("td"); td.className = "num";
      const v = perCat.get(m)?.get(o.code);
      td.textContent = v ? NFD.format(Math.round(v * 100) / 100) : "—";
      tr.appendChild(td);
    }
    const slot = per.get(m);
    const tdT = document.createElement("td"); tdT.className = "num strong";
    tdT.textContent = NFD.format(Math.round(slot.sum * 100) / 100); tr.appendChild(tdT);
    const tdP = document.createElement("td"); tdP.className = "num";
    tdP.textContent = slot.qty; tr.appendChild(tdP);
    tbl.appendChild(tr);
  }
  const trT = document.createElement("tr");
  const tdT0 = document.createElement("td"); tdT0.className = "strong"; tdT0.textContent = "Итого"; trT.appendChild(tdT0);
  for (const o of DATA.order) {
    const td = document.createElement("td"); td.className = "num strong";
    let s = 0; for (const m of buckets) s += perCat.get(m)?.get(o.code) || 0;
    td.textContent = s ? NFD.format(Math.round(s * 100) / 100) : "—"; trT.appendChild(td);
  }
  const tdTT = document.createElement("td"); tdTT.className = "num strong";
  tdTT.textContent = NFD.format(Math.round(agg.total * 100) / 100); trT.appendChild(tdTT);
  const tdTP = document.createElement("td"); tdTP.className = "num strong";
  tdTP.textContent = items.length; trT.appendChild(tdTP);
  tbl.appendChild(trT);
  matrix.appendChild(tbl);

  const recv = document.getElementById("recv");
  recv.textContent = "";
  const rlist = DATA.receipts.filter(r => r.total != null && r.d);
  const nd = days ? (+new Date(DATA.maxDate + "T00:00:00") - days * 86400000) : -Infinity;
  const shown = rlist.filter(r => days ? +new Date(r.d.slice(0, 10) + "T00:00:00") >= nd : true);
  const rt = document.createElement("table");
  const rh = document.createElement("tr");
  for (const h of ["Дата", "Магазин", "Сумма"]) {
    const th = document.createElement("th"); if (h === "Сумма") th.className = "num"; th.textContent = h; rh.appendChild(th);
  }
  rt.appendChild(rh);
  for (const r of shown) {
    const tr = document.createElement("tr");
    const d1 = document.createElement("td");
    const dd = new Date(r.d.replace(" ", "T"));
    d1.textContent = dd.toLocaleDateString("ru-RU", {day: "2-digit", month: "2-digit", year: "numeric"}) +
      (r.d.includes(" ") ? " " + r.d.slice(11, 16) : "");
    tr.appendChild(d1);
    const d2 = document.createElement("td"); d2.textContent = r.shop || "—"; tr.appendChild(d2);
    const d3 = document.createElement("td"); d3.className = "num";
    d3.textContent = r.total != null ? NFD.format(r.total) + " ₽" : "—"; tr.appendChild(d3);
    rt.appendChild(tr);
  }
  recv.appendChild(rt);
}

function bucketList() {
  return aggUnit === "week" ? Array.from(agg.byWeek.keys()).sort()
                            : Array.from(agg.byMonth.keys()).sort(monthSort);
}
function bucketsAgg() {
  return aggUnit === "week" ? {per: agg.byWeek, perCat: agg.byWeekCat}
                            : {per: agg.byMonth, perCat: agg.byMonthCat};
}
function bucketShort(k) {
  return aggUnit === "week" ? k.replace(/^\d{4}-/, "") : k;  // «W41» вместо «2026-W41»
}

function render() {
  const items = slice(DATA.items, days);
  agg = aggregate(items);

  document.getElementById("meta").textContent =
    "Данные: 22.07.2026 — " + new Date(DATA.maxDate + "T00:00:00").toLocaleDateString("ru-RU", {day: "2-digit", month: "2-digit", year: "numeric"}) +
    "  ·  сформировано: " + DATA.generated;
  document.getElementById("foot").textContent =
    "Файл построен автоматически из data/positions.json (python src/build_dashboard.py). Всплывающие подсказки не заменяют таблицу — полные значения ниже в блоке «Таблица: статьи × " + (aggUnit === "week" ? "недели" : "месяцы") + "» и «Чеки за период».";

  const periodWord = aggUnit === "week" ? "неделям" : "месяцам";
  document.getElementById("h2-months").textContent = "Расходы по " + periodWord;
  document.getElementById("h2-matrix").textContent = "Статьи × " + (aggUnit === "week" ? "недели" : "месяцы");
  document.getElementById("h2-table").textContent = "Таблица: статьи × " + (aggUnit === "week" ? "недели" : "месяцы");
  document.getElementById("sub-matrix").textContent = aggUnit === "week"
    ? "столбцы стеком по неделям (пн–вс) · один tooltip на неделю"
    : "столбцы стеком · один tooltip на месяц";

  // KPI
  const k = document.getElementById("kpis");
  k.textContent = "";
  const receiptsCount = new Set(items.map(it => it.rid)).size;
  const monthsCount = Array.from(agg.byMonth.keys()).length;
  const weeksCount = Array.from(agg.byWeek.keys()).length;
  const tiles = [
    {label: "Итого за выбранный период", value: rub(Math.round(agg.total * 100) / 100), big: true,
     note: monthsCount + " мес. · " + weeksCount + " нед." + " · " + receiptsCount + " чек." + (agg.undatedSum ? " · есть позиции без даты" : "")},
    {label: "Позиций", value: String(items.length), note: "по всем чекам"},
    {label: "Средний чек", value: rub(receiptsCount ? Math.round(agg.total / receiptsCount * 100) / 100 : 0), note: "итог÷чеки"},
    {label: "Топ-статья", value: topCat(items), note: "наибольшая сумма"},
  ];
  for (const t of tiles) {
    const tile = document.createElement("div"); tile.className = "tile";
    const l = document.createElement("div"); l.className = "label"; l.textContent = t.label; tile.appendChild(l);
    const v = document.createElement("div"); v.className = "value" + (t.big ? " big" : ""); v.textContent = t.value; tile.appendChild(v);
    const n = document.createElement("div"); n.className = "note"; n.textContent = t.note; tile.appendChild(n);
    k.appendChild(tile);
  }

  // столбцы: месяцы или недели (по переключателю «Разрез»)
  const buckets = bucketList();
  const {per, perCat} = bucketsAgg();
  const bucketTotals = buckets.map(m => Math.round(per.get(m).sum * 100) / 100);
  const maxY = Math.max(...bucketTotals, 1);
  renderMonths(document.getElementById("months"),
               buckets.map(bucketShort), bucketTotals, maxY, days);
  renderCumulative(document.getElementById("cumulative"), agg.cum);

  // статьи (гор. бары)
  const catRows = DATA.order.map((o, i) => {
    const c = agg.byCat.get(o.code) || {sum: 0, qty: 0};
    return {code: o.code, name: o.name, idx: i, sum: Math.round(c.sum * 100) / 100, qty: c.qty};
  }).sort((a, b) => b.sum - a.sum);
  renderCats(document.getElementById("cats"), catRows);

  // стек
  renderLegend(document.getElementById("legend"));
  const usedCodes = DATA.order.filter(o => buckets.some(m => perCat.get(m)?.get(o.code)));
  const series = usedCodes.map((o, i) => ({
    code: o.code, idx: slotIndex.get(o.code), total: 0,
    values: buckets.map(m => Math.round((perCat.get(m)?.get(o.code) || 0) * 100) / 100),
  }));
  series.forEach(s => s.total = Math.max(...s.values, 0));
  renderStack(document.getElementById("stack"), buckets, series);

  renderTables(items);
}

function topCat(items) {
  const byCat = new Map();
  for (const it of items) byCat.set(it.code, (byCat.get(it.code) || 0) + it.sum);
  let best = null, bestSum = -1;
  for (const [code, sum] of byCat) if (sum > bestSum) { bestSum = sum; best = code; }
  const o = DATA.order.find(x => x.code === best);
  return o ? o.name : "—";
}

// фильтры: период (слева) и разрез периодических графиков (месяц/неделя)
const FILTERS = [[0, "Всё время"], [7, "7 дней"], [30, "30 дней"]];
function applyFilter(i) {
  days = FILTERS[i][0];
  document.querySelectorAll(".fbtn.fperiod").forEach((b, bi) => b.setAttribute("aria-pressed", String(bi === i)));
  render();
}
function applyAgg(i) {
  aggUnit = i === 0 ? "month" : "week";
  document.querySelectorAll(".fbtn.fagg").forEach((b, bi) => b.setAttribute("aria-pressed", String(bi === i)));
  render();
}
const fnav = document.getElementById("filters");
FILTERS.forEach(([d, label], i) => {
  const b = document.createElement("button");
  b.type = "button"; b.className = "fbtn fperiod"; b.setAttribute("aria-pressed", String(i === 0));
  const chk = document.createElement("span"); chk.className = "chk"; chk.textContent = "✓"; b.appendChild(chk);
  b.appendChild(document.createTextNode(label));
  b.addEventListener("click", () => applyFilter(i));
  fnav.appendChild(b);
});
const aggHint = document.createElement("span");
aggHint.className = "fsep";
aggHint.textContent = "Разрез:";
fnav.appendChild(aggHint);
[["Месяц", "month"], ["Неделя", "week"]].forEach(([label], i) => {
  const b = document.createElement("button");
  b.type = "button"; b.className = "fbtn fagg"; b.setAttribute("aria-pressed", String(i === 0));
  const chk = document.createElement("span"); chk.className = "chk"; chk.textContent = "✓"; b.appendChild(chk);
  b.appendChild(document.createTextNode(label));
  b.addEventListener("click", () => applyAgg(i));
  fnav.appendChild(b);
});
applyTheme();
applyFilter(0);
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="HTML-дашборд по чекам")
    parser.add_argument("--input", default=POSITIONS)
    parser.add_argument("--output", default=OUTPUT)
    args = parser.parse_args()

    html = HTML.replace("__DATA__", build_data(args.input))
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"Дашборд: {args.output} ({os.path.getsize(args.output)} байт)")


if __name__ == "__main__":
    main()