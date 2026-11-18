# -*- coding: utf-8 -*-
"""Приёмник файлов чеков с защитой от дублей.

Любой источник (Telegram-бот, облачная папка, ручная загрузка) кладёт файлы
через этот модуль. Дубликаты определяются по SHA-256 содержимого, поэтому
повторно присланный или синхронизированный чек не обработается дважды.

Реестр обработанных файлов: data/seen_hashes.json
"""

import argparse
import hashlib
import json
import os
import shutil
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
RECEIPTS = os.path.join(ROOT, "receipts")
DATA = os.path.join(ROOT, "data")
HASH_FILE = os.path.join(DATA, "seen_hashes.json")
PIPELINE = os.path.join(CODE_ROOT, "src", "run_pipeline.py")

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".pdf", ".heic"}


def sha256(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def save_registry(registry, hash_file=None):
    os.makedirs(DATA, exist_ok=True)
    with open(hash_file or HASH_FILE, "w", encoding="utf-8") as fh:
        json.dump(registry, fh, ensure_ascii=False, indent=2)


def seed_registry(registry, receipts_dir=None, hash_file=None):
    """Первый запуск: считаем уже лежащие в приёме файлы известными."""
    receipts_dir = receipts_dir or RECEIPTS
    if registry:
        return registry
    if not os.path.isdir(receipts_dir):
        return registry
    added = 0
    for name in sorted(os.listdir(receipts_dir)):
        path = os.path.join(receipts_dir, name)
        if os.path.isfile(path) and os.path.splitext(name)[1].lower() in EXTS:
            registry[sha256(path)] = {
                "name": name,
                "source": "existing",
                "added": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
            added += 1
    if added:
        save_registry(registry, hash_file)
    return registry


def unique_target(name, receipts_dir=None):
    receipts_dir = receipts_dir or RECEIPTS
    os.makedirs(receipts_dir, exist_ok=True)
    stem, ext = os.path.splitext(name)
    candidate = os.path.join(receipts_dir, name)
    counter = 2
    while os.path.exists(candidate):
        candidate = os.path.join(receipts_dir, f"{stem} ({counter}){ext}")
        counter += 1
    return candidate


def load_registry(hash_file=None):
    path = hash_file or HASH_FILE
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def ingest(src_path, source="manual", move=False, name=None, receipts_dir=None, hash_file=None):
    """Кладёт файл в указанную папку приёма; реестр дублей — из hash_file."""
    registry = seed_registry(load_registry(hash_file), receipts_dir, hash_file)
    if not os.path.isfile(src_path):
        return {"status": "error", "reason": "файл не найден", "path": src_path}

    ext = os.path.splitext(src_path)[1].lower()
    if ext not in EXTS:
        return {"status": "skipped", "reason": f"неподдерживаемый формат {ext}", "path": src_path}

    digest = sha256(src_path)
    if digest in registry:
        return {"status": "duplicate", "name": registry[digest]["name"], "hash": digest}

    target_name = name or os.path.basename(src_path)
    target = unique_target(target_name, receipts_dir)
    if move:
        shutil.move(src_path, target)
    else:
        shutil.copy2(src_path, target)

    registry[digest] = {
        "name": os.path.basename(target),
        "source": source,
        "added": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    save_registry(registry, hash_file)
    return {"status": "added", "name": os.path.basename(target), "target": target, "hash": digest}


def run_pipeline(data_root=None):
    """Запускает подготовку изображений и пересборку отчёта.

    Вывод дочерних процессов читается толерантно: консольные сообщения
    Windows приходят не в UTF-8, и жёсткий декодинг ронял вызывающий процесс.
    """
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    if data_root:
        env = dict(env, DATA_ROOT=data_root)
    try:
        return subprocess.run([sys.executable, PIPELINE], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", env=env)
    except Exception as exc:
        print(f"Ошибка запуска конвейера: {exc!r}")
        return None


def sweep(folder, source="folder", move=False, run=False):
    results = []
    if not os.path.isdir(folder):
        print(f"Папка не найдена: {folder}")
        return results
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            results.append(ingest(path, source=source, move=move))
    for res in results:
        print(f"{res['status']:10} {res.get('name') or res.get('path')}")
    added = sum(1 for r in results if r["status"] == "added")
    print(f"\nВсего: {len(results)}, новых: {added}")
    if run and added:
        print("\nЗапуск конвейера...")
        proc = run_pipeline()
        print(proc.stdout or "", proc.stderr or "")
    return results


def main():
    parser = argparse.ArgumentParser(description="Приём чеков в папку receipts/")
    parser.add_argument("--from", dest="folder", help="папка-источник для перебора файлов")
    parser.add_argument("--source", default="folder", help="метка источника")
    parser.add_argument("--move", action="store_true", help="переносить, а не копировать")
    parser.add_argument("--run", action="store_true", help="запустить конвейер при наличии новых файлов")
    args = parser.parse_args()

    if args.folder:
        sweep(args.folder, source=args.source, move=args.move, run=args.run)
    else:
        registry = seed_registry(load_registry())
        print(f"В реестре файлов: {len(registry)}")
        print(f"Папка приёма: {RECEIPTS}")


if __name__ == "__main__":
    main()
