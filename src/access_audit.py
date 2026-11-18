# -*- coding: utf-8 -*-
"""Аудит изоляции групп: имитирует сообщения от каждого аккаунта и доказывает,
что бот выдаёт только файлы/данные своей группы.

Запуск: python src/access_audit.py
Выход: строки PASS/FAIL по каждому аккаунту; процесс завершается кодом 1 при FAIL.
"""

import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC)

import telegram_bot as tb  # noqa: E402


def audit():
    results = []

    sent_files = []
    tb.api_post_file = lambda method, params, field, path: sent_files.append(
        (params.get("chat_id"), os.path.abspath(path))) or {"ok": True}

    sent_msgs = {}
    tb.send_message = lambda cid, text: sent_msgs.setdefault(cid, []).append(str(text)) or None

    real_pipeline = tb.run_pipeline
    tb.run_pipeline = lambda *a, **k: type("P", (), {"stdout": "audit"})()

    real_download = tb.download_file
    tb.download_file = lambda fid, name, tmp_dir=None: real_download(fid, name, tmp_dir=tmp_dir)

    sent_files.clear()
    sent_msgs.clear()

    # 1) незнакомый аккаунт не должен получить ДАННЫЕ, только отказ с его id
    tb.handle_message({"chat": {"id": 999999}, "from": {"id": 999999}, "text": "/status"})
    ok = len(sent_msgs.get(999999, [])) == 1 and "chat_id = 999999" in sent_msgs[999999][0]
    results.append(("PASS" if ok else "FAIL",
                    "незнакомый получает только отказ со своим chat_id"))

    accounts = []
    for gcode, g in tb.load_access().get("groups", {}).items():
        for uid in (g.get("users") or []) or [None]:
            username = (g.get("usernames") or [None])[0]
            accounts.append((gcode, uid, username))

    # 2) по каждому известному аккаунту: все команды — только своих путей
    for gcode, uid, uname in accounts:
        if uid is None and uname is None:
            continue
        sent_files.clear()
        sent_msgs.clear()
        message = {"chat": {"id": uid}, "from": {"id": uid, "username": uname}}
        tb.handle_message({**message, "text": "/status"})
        tb.handle_message({**message, "text": "/run"})
        if os.path.exists(tb.store_paths(tb.group_store(gcode))["positions"]):
            tb.handle_message({**message, "text": "/report"})
            tb.handle_message({**message, "text": "/dash"})
        own = os.path.join(tb.STORE_BASE, gcode)
        other_files = [f for _, f in sent_files if os.path.commonpath([f, os.path.abspath(own)]) != os.path.abspath(own)]
        ok = not other_files
        results.append(("PASS" if ok else "FAIL",
                        f"{gcode}(chat {uid}): все присланное из своей группы"
                        + ("" if ok else f" — УТЕЧКА: {other_files}")))
        # статус не должен называть чужие группы
        status_text = " ".join(sent_msgs.get(uid, []))
        foreign = [g for g in tb.load_access()["groups"] if g != gcode and g in status_text]
        ok2 = not foreign
        results.append(("PASS" if ok2 else "FAIL",
                        f"{gcode}(chat {uid}): статус не называет чужих групп"
                        + ("" if ok2 else f" — упоминаются: {foreign}")))
        # ни один отправленный файл не лежит в чужой группе
        leaks = []
        for _, f in sent_files:
            parts = f.replace("\\", "/").split("/")
            if "groups" in parts:
                code = parts[parts.index("groups") + 1] if parts.index("groups") + 1 < len(parts) else ""
                if code and code != gcode:
                    leaks.append(code)
        if leaks:
            results.append(("FAIL", f"{gcode}: утечка файлов из чужих групп {set(leaks)}"))
    for status, text in results:
        print(f"{status}  {text}")
    return any(s == "FAIL" for s, _ in results)


if __name__ == "__main__":
    sys.exit(1 if audit() else 0)