"""Переносит данные из локальной SQLite-базы в Postgres из DATABASE_URL.

Запуск из корня проекта:
    python -m scripts.migrate_to_postgres [путь к bot.db]
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv

from bot.db import TABLES, Database

ROOT = Path(__file__).resolve().parent.parent
DATA_TABLES = ("promos", "sales", "payouts", "payments")


def read_sqlite(path: Path) -> dict[str, list[dict]]:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
            for table in TABLES
        }
    finally:
        conn.close()


async def promo_summary(db: Database) -> dict[str, tuple[int, int, int]]:
    result = {}
    for promo in await db.list_promos():
        stats = await db.promo_stats(promo.id)
        result[promo.code] = (stats.sales_total, stats.earned, stats.paid)
    return result


async def main() -> int:
    load_dotenv(ROOT / ".env")
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL не задан в .env — некуда переносить.")
        return 1
    source_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "bot.db"
    if not source_path.exists():
        print(f"Файл {source_path} не найден.")
        return 1

    data = read_sqlite(source_path)
    print("SQLite:", {table: len(rows) for table, rows in data.items()})

    target = Database(url=url)
    await target.connect()
    try:
        print("Postgres:", target.describe())
        counts = await target.table_counts()
        busy = {table: counts[table] for table in DATA_TABLES if counts[table]}
        if busy:
            print(f"В Postgres уже есть данные {busy}. Перенос отменён, чтобы ничего не задвоить.")
            return 1

        for table in DATA_TABLES:
            await target.import_rows(table, data[table])
        for row in data["settings"]:
            await target.set_setting(row["key"], row["value"])
        await target.after_import()

        counts = await target.table_counts()
        print("Перенесено:", counts)
        mismatch = [t for t in DATA_TABLES if counts[t] != len(data[t])]
        if mismatch:
            print("Количество строк не совпало:", mismatch)
            return 1

        source = Database(path=source_path)
        await source.connect()
        try:
            before = await promo_summary(source)
        finally:
            await source.close()
        after = await promo_summary(target)
        if before != after:
            print("Статистика по промокодам отличается!")
            for code in sorted(set(before) | set(after)):
                if before.get(code) != after.get(code):
                    print(f"  {code}: было {before.get(code)}, стало {after.get(code)}")
            return 1
        print(f"Статистика совпадает по всем {len(after)} промокодам. Готово.")
        return 0
    finally:
        await target.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
