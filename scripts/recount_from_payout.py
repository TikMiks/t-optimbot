# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.db import Database
from bot.utils import fmt_dt

MSK = timezone(timedelta(hours=3))
CUTOFF = int(datetime(2026, 9, 26, 12, 31, tzinfo=MSK).timestamp())
COMMENT = "Выплата 26.09.2026 12:31"


async def main() -> None:
    db = Database(Path("data/bot.db"))
    await db.connect()
    print("cutoff", fmt_dt(CUTOFF, 3), CUTOFF)

    async with db.conn.execute(
        """
        SELECT p.id AS promo_id, p.code, s.id AS sale_id, s.created_at, s.reward,
               COALESCE(s.is_reverted, 0) AS is_reverted
          FROM sales s
          JOIN promos p ON p.id = s.promo_id
         ORDER BY s.created_at, p.code
        """
    ) as cur:
        rows = await cur.fetchall()

    before: dict[int, int] = {}
    codes: dict[int, str] = {}
    after_count = 0
    for row in rows:
        codes[row["promo_id"]] = row["code"]
        mark = "AFTER" if row["created_at"] > CUTOFF else "BEFORE"
        print(
            f"{mark}\t{row['code']}\t{fmt_dt(row['created_at'], 3)}\t"
            f"{row['reward']}\treverted={row['is_reverted']}"
        )
        if row["created_at"] <= CUTOFF and not row["is_reverted"]:
            before[row["promo_id"]] = before.get(row["promo_id"], 0) + row["reward"]
        if row["created_at"] > CUTOFF and not row["is_reverted"]:
            after_count += 1

    # убрать прошлые авто-выплаты с этой же датой, если скрипт запускают повторно
    await db.conn.execute(
        "DELETE FROM payouts WHERE comment = ? AND created_at = ?",
        (COMMENT, CUTOFF),
    )

    for promo in await db.list_promos():
        amount = before.get(promo.id, 0)
        await db.conn.execute(
            """
            INSERT INTO payouts (promo_id, amount, comment, created_by, created_at)
            VALUES (?, ?, ?, NULL, ?)
            """,
            (promo.id, amount, COMMENT, CUTOFF),
        )
        print(f"payout {promo.code} {amount}")

    await db.conn.commit()

    print("--- после пересчёта ---")
    for promo in await db.list_promos():
        stats = await db.promo_stats(promo.id)
        print(
            f"{promo.code}: sales={stats.sales_total} "
            f"earned={stats.earned} paid={stats.paid} "
            f"due={stats.balance} last={fmt_dt(stats.last_payout_at, 3)}"
        )
    print("продаж после отсечки:", after_count)
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
