from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from bot.utils import new_token, now_ts

SCHEMA = """
CREATE TABLE IF NOT EXISTS promos (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    code           TEXT    NOT NULL UNIQUE,
    reward         INTEGER NOT NULL,
    owner_id       INTEGER,
    owner_username TEXT,
    owner_name     TEXT,
    bind_token     TEXT    UNIQUE,
    is_active      INTEGER NOT NULL DEFAULT 1,
    created_at     INTEGER NOT NULL,
    bound_at       INTEGER,
    hidden_in_report INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sales (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    promo_id    INTEGER NOT NULL REFERENCES promos(id) ON DELETE CASCADE,
    amount      INTEGER,
    reward      INTEGER NOT NULL,
    note        TEXT,
    created_by  INTEGER,
    created_at  INTEGER NOT NULL,
    external_id TEXT    UNIQUE,
    is_reverted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS payouts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    promo_id   INTEGER NOT NULL REFERENCES promos(id) ON DELETE CASCADE,
    amount     INTEGER NOT NULL,
    comment    TEXT,
    created_by INTEGER,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id    TEXT    NOT NULL UNIQUE,
    promo_id          INTEGER REFERENCES promos(id) ON DELETE SET NULL,
    sale_id           INTEGER REFERENCES sales(id) ON DELETE SET NULL,
    amount            INTEGER NOT NULL DEFAULT 0,
    currency          TEXT    NOT NULL DEFAULT 'RUB',
    status            TEXT    NOT NULL,
    payload           TEXT,
    description       TEXT,
    payment_method    TEXT,
    promo_code        TEXT,
    created_at        INTEGER NOT NULL,
    processed_at      INTEGER,
    platega_created_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_promos_owner    ON promos(owner_id);
CREATE INDEX IF NOT EXISTS idx_sales_promo     ON sales(promo_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_payouts_promo   ON payouts(promo_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status, promo_id);
"""

SALE_ACTIVE = "COALESCE(is_reverted, 0) = 0"

DAY = 86_400
# Отсчёт текущего отчёта: выплата 26.09.2026 12:31 MSK
DEFAULT_REPORT_SINCE = 1_790_415_060
REPORT_SINCE_KEY = "report_since"


@dataclass(slots=True)
class Promo:
    id: int
    code: str
    reward: int
    owner_id: int | None
    owner_username: str | None
    owner_name: str | None
    bind_token: str | None
    is_active: bool
    created_at: int
    bound_at: int | None
    hidden_in_report: bool = False

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "Promo":
        keys = row.keys()
        return cls(
            id=row["id"],
            code=row["code"],
            reward=row["reward"],
            owner_id=row["owner_id"],
            owner_username=row["owner_username"],
            owner_name=row["owner_name"],
            bind_token=row["bind_token"],
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
            bound_at=row["bound_at"],
            hidden_in_report=bool(row["hidden_in_report"])
            if "hidden_in_report" in keys
            else False,
        )


@dataclass(slots=True)
class PromoStats:
    sales_total: int = 0
    sales_7d: int = 0
    sales_30d: int = 0
    earned: int = 0
    revenue: int = 0
    paid: int = 0
    last_sale_at: int | None = None
    last_payout_at: int | None = None
    last_payout_amount: int | None = None

    @property
    def balance(self) -> int:
        return self.earned - self.paid


@dataclass(slots=True)
class SaleRecord:
    id: int
    amount: int | None
    reward: int
    note: str | None
    created_at: int
    is_reverted: bool = False


@dataclass(slots=True)
class PaymentRecord:
    id: int
    transaction_id: str
    promo_id: int | None
    sale_id: int | None
    amount: int
    currency: str
    status: str
    payload: str | None
    description: str | None
    payment_method: str | None
    promo_code: str | None
    created_at: int
    processed_at: int | None
    platega_created_at: str | None

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "PaymentRecord":
        return cls(
            id=row["id"],
            transaction_id=row["transaction_id"],
            promo_id=row["promo_id"],
            sale_id=row["sale_id"],
            amount=row["amount"],
            currency=row["currency"],
            status=row["status"],
            payload=row["payload"],
            description=row["description"],
            payment_method=row["payment_method"],
            promo_code=row["promo_code"],
            created_at=row["created_at"],
            processed_at=row["processed_at"],
            platega_created_at=row["platega_created_at"],
        )


@dataclass(slots=True)
class PayoutRecord:
    id: int
    amount: int
    comment: str | None
    created_at: int


class PromoCodeExists(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Database:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() ещё не вызван")
        return self._conn

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        await self._migrate()
        if await self.get_setting(REPORT_SINCE_KEY) is None:
            await self.set_setting(REPORT_SINCE_KEY, str(DEFAULT_REPORT_SINCE))
        await self._conn.commit()

    async def _migrate(self) -> None:
        """Добавляет колонки, которых нет в уже существующей базе."""
        await self._ensure_column("sales", "external_id", "TEXT")
        await self._ensure_column("sales", "is_reverted", "INTEGER NOT NULL DEFAULT 0")
        await self._ensure_column(
            "promos", "hidden_in_report", "INTEGER NOT NULL DEFAULT 0"
        )
        await self._drop_column("promos", "discount_percent")
        await self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_sales_external "
            "ON sales(external_id) WHERE external_id IS NOT NULL"
        )

    async def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        async with self.conn.execute(f"PRAGMA table_info({table})") as cur:
            existing = {row["name"] for row in await cur.fetchall()}
        if column not in existing:
            await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    async def _drop_column(self, table: str, column: str) -> None:
        async with self.conn.execute(f"PRAGMA table_info({table})") as cur:
            existing = {row["name"] for row in await cur.fetchall()}
        if column in existing:
            await self.conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # ---------------------------------------------------------------- promos

    async def create_promo(self, code: str, reward: int) -> Promo:
        try:
            cursor = await self.conn.execute(
                """
                INSERT INTO promos (code, reward, bind_token, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (code, reward, new_token(), now_ts()),
            )
        except aiosqlite.IntegrityError as exc:
            raise PromoCodeExists(code) from exc
        await self.conn.commit()
        promo = await self.get_promo_by_id(cursor.lastrowid)
        assert promo is not None
        return promo

    async def get_promo_by_id(self, promo_id: int) -> Promo | None:
        async with self.conn.execute("SELECT * FROM promos WHERE id = ?", (promo_id,)) as cur:
            row = await cur.fetchone()
        return Promo.from_row(row) if row else None

    async def get_promo_by_code(self, code: str) -> Promo | None:
        async with self.conn.execute("SELECT * FROM promos WHERE code = ?", (code,)) as cur:
            row = await cur.fetchone()
        return Promo.from_row(row) if row else None

    async def get_promo_by_token(self, token: str) -> Promo | None:
        async with self.conn.execute(
            "SELECT * FROM promos WHERE bind_token = ?", (token,)
        ) as cur:
            row = await cur.fetchone()
        return Promo.from_row(row) if row else None

    async def list_promos(self) -> list[Promo]:
        async with self.conn.execute("SELECT * FROM promos ORDER BY code") as cur:
            rows = await cur.fetchall()
        return [Promo.from_row(row) for row in rows]

    async def list_promos_by_owner(self, owner_id: int) -> list[Promo]:
        async with self.conn.execute(
            "SELECT * FROM promos WHERE owner_id = ? ORDER BY code", (owner_id,)
        ) as cur:
            rows = await cur.fetchall()
        return [Promo.from_row(row) for row in rows]

    async def set_reward(self, promo_id: int, reward: int) -> None:
        await self.conn.execute(
            "UPDATE promos SET reward = ? WHERE id = ?", (reward, promo_id)
        )
        await self.conn.commit()

    async def set_active(self, promo_id: int, is_active: bool) -> None:
        await self.conn.execute(
            "UPDATE promos SET is_active = ? WHERE id = ?", (int(is_active), promo_id)
        )
        await self.conn.commit()

    async def set_hidden_in_report(self, promo_id: int, hidden: bool) -> None:
        await self.conn.execute(
            "UPDATE promos SET hidden_in_report = ? WHERE id = ?",
            (int(hidden), promo_id),
        )
        await self.conn.commit()

    async def delete_promo(self, promo_id: int) -> None:
        await self.conn.execute("DELETE FROM promos WHERE id = ?", (promo_id,))
        await self.conn.commit()

    async def reset_bind_token(self, promo_id: int) -> str:
        token = new_token()
        await self.conn.execute(
            "UPDATE promos SET bind_token = ? WHERE id = ?", (token, promo_id)
        )
        await self.conn.commit()
        return token

    async def bind_owner(
        self, promo_id: int, owner_id: int, username: str | None, name: str | None
    ) -> None:
        """Привязывает владельца и гасит одноразовый токен."""
        await self.conn.execute(
            """
            UPDATE promos
               SET owner_id = ?, owner_username = ?, owner_name = ?,
                   bound_at = ?, bind_token = NULL
             WHERE id = ?
            """,
            (owner_id, username, name, now_ts(), promo_id),
        )
        await self.conn.commit()

    async def unbind_owner(self, promo_id: int) -> str:
        token = new_token()
        await self.conn.execute(
            """
            UPDATE promos
               SET owner_id = NULL, owner_username = NULL, owner_name = NULL,
                   bound_at = NULL, bind_token = ?
             WHERE id = ?
            """,
            (token, promo_id),
        )
        await self.conn.commit()
        return token

    async def refresh_owner_profile(
        self, owner_id: int, username: str | None, name: str | None
    ) -> None:
        await self.conn.execute(
            "UPDATE promos SET owner_username = ?, owner_name = ? WHERE owner_id = ?",
            (username, name, owner_id),
        )
        await self.conn.commit()

    # ----------------------------------------------------------------- sales

    async def add_sale(
        self,
        promo_id: int,
        reward: int,
        amount: int | None = None,
        note: str | None = None,
        created_by: int | None = None,
        external_id: str | None = None,
        created_at: int | None = None,
    ) -> int:
        cursor = await self.conn.execute(
            """
            INSERT INTO sales (
                promo_id, amount, reward, note, created_by, created_at, external_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (promo_id, amount, reward, note, created_by, created_at or now_ts(), external_id),
        )
        await self.conn.commit()
        return int(cursor.lastrowid)

    async def delete_last_sale(self, promo_id: int) -> SaleRecord | None:
        async with self.conn.execute(
            f"""
            SELECT id, amount, reward, note, created_at, COALESCE(is_reverted, 0) AS is_reverted
              FROM sales
             WHERE promo_id = ? AND {SALE_ACTIVE}
             ORDER BY id DESC
             LIMIT 1
            """,
            (promo_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        await self.conn.execute("DELETE FROM sales WHERE id = ?", (row["id"],))
        await self.conn.commit()
        return SaleRecord(
            id=row["id"],
            amount=row["amount"],
            reward=row["reward"],
            note=row["note"],
            created_at=row["created_at"],
            is_reverted=bool(row["is_reverted"]),
        )

    async def revert_sale(self, sale_id: int) -> SaleRecord | None:
        async with self.conn.execute(
            """
            SELECT id, amount, reward, note, created_at, COALESCE(is_reverted, 0) AS is_reverted
              FROM sales
             WHERE id = ?
            """,
            (sale_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        if not row["is_reverted"]:
            await self.conn.execute(
                "UPDATE sales SET is_reverted = 1 WHERE id = ?", (sale_id,)
            )
            await self.conn.commit()
        return SaleRecord(
            id=row["id"],
            amount=row["amount"],
            reward=row["reward"],
            note=row["note"],
            created_at=row["created_at"],
            is_reverted=True,
        )

    async def recent_sales(self, promo_id: int, limit: int = 10) -> list[SaleRecord]:
        async with self.conn.execute(
            f"""
            SELECT id, amount, reward, note, created_at, COALESCE(is_reverted, 0) AS is_reverted
              FROM sales
             WHERE promo_id = ? AND {SALE_ACTIVE} AND created_at > ?
             ORDER BY created_at DESC, id DESC
             LIMIT ?
            """,
            (promo_id, await self.get_report_since(), limit),
        ) as cur:
            rows = await cur.fetchall()
        return [
            SaleRecord(
                id=row["id"],
                amount=row["amount"],
                reward=row["reward"],
                note=row["note"],
                created_at=row["created_at"],
                is_reverted=bool(row["is_reverted"]),
            )
            for row in rows
        ]

    # --------------------------------------------------------------- payouts

    async def add_payout(
        self,
        promo_id: int,
        amount: int,
        comment: str | None = None,
        created_by: int | None = None,
        created_at: int | None = None,
    ) -> int:
        cursor = await self.conn.execute(
            """
            INSERT INTO payouts (promo_id, amount, comment, created_by, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (promo_id, amount, comment, created_by, created_at or now_ts()),
        )
        await self.conn.commit()
        return int(cursor.lastrowid)

    async def recent_payouts(self, promo_id: int, limit: int = 10) -> list[PayoutRecord]:
        async with self.conn.execute(
            """
            SELECT id, amount, comment, created_at
              FROM payouts
             WHERE promo_id = ?
             ORDER BY created_at DESC, id DESC
             LIMIT ?
            """,
            (promo_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [
            PayoutRecord(
                id=row["id"],
                amount=row["amount"],
                comment=row["comment"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    # ------------------------------------------------------------------ stats

    async def promo_stats(self, promo_id: int) -> PromoStats:
        ts = now_ts()
        since = await self.get_report_since()
        async with self.conn.execute(
            """
            SELECT COUNT(*)                                          AS sales_total,
                   COALESCE(SUM(reward), 0)                          AS earned,
                   COALESCE(SUM(amount), 0)                          AS revenue,
                   COALESCE(SUM(created_at >= ?), 0)                 AS sales_7d,
                   COALESCE(SUM(created_at >= ?), 0)                 AS sales_30d,
                   MAX(created_at)                                   AS last_sale_at
              FROM sales
             WHERE promo_id = ? AND COALESCE(is_reverted, 0) = 0
               AND created_at > ?
            """,
            (ts - 7 * DAY, ts - 30 * DAY, promo_id, since),
        ) as cur:
            sales = await cur.fetchone()

        async with self.conn.execute(
            """
            SELECT COALESCE(SUM(CASE WHEN created_at > ? THEN amount ELSE 0 END), 0) AS paid,
                   MAX(created_at) AS last_payout_at
              FROM payouts
             WHERE promo_id = ?
            """,
            (since, promo_id),
        ) as cur:
            payouts = await cur.fetchone()

        last_payout_amount: int | None = None
        if payouts["last_payout_at"]:
            async with self.conn.execute(
                """
                SELECT amount FROM payouts
                 WHERE promo_id = ?
                 ORDER BY created_at DESC, id DESC
                 LIMIT 1
                """,
                (promo_id,),
            ) as cur:
                row = await cur.fetchone()
            last_payout_amount = row["amount"] if row else None

        return PromoStats(
            sales_total=sales["sales_total"],
            sales_7d=sales["sales_7d"],
            sales_30d=sales["sales_30d"],
            earned=sales["earned"],
            revenue=sales["revenue"],
            paid=payouts["paid"],
            last_sale_at=sales["last_sale_at"],
            last_payout_at=payouts["last_payout_at"],
            last_payout_amount=last_payout_amount,
        )

    async def global_stats(self) -> dict[str, int]:
        ts = now_ts()
        since = await self.get_report_since()
        async with self.conn.execute(
            """
            SELECT (SELECT COUNT(*) FROM promos WHERE COALESCE(hidden_in_report, 0) = 0) AS promos_total,
                   (SELECT COUNT(*) FROM promos WHERE owner_id IS NOT NULL AND COALESCE(hidden_in_report, 0) = 0) AS promos_bound,
                   (SELECT COUNT(*) FROM sales s JOIN promos p ON p.id = s.promo_id
                     WHERE COALESCE(s.is_reverted, 0) = 0 AND COALESCE(p.hidden_in_report, 0) = 0 AND s.created_at > ?) AS sales_total,
                   (SELECT COALESCE(SUM(s.reward), 0) FROM sales s JOIN promos p ON p.id = s.promo_id
                     WHERE COALESCE(s.is_reverted, 0) = 0 AND COALESCE(p.hidden_in_report, 0) = 0 AND s.created_at > ?) AS earned,
                   (SELECT COALESCE(SUM(s.amount), 0) FROM sales s JOIN promos p ON p.id = s.promo_id
                     WHERE COALESCE(s.is_reverted, 0) = 0 AND COALESCE(p.hidden_in_report, 0) = 0 AND s.created_at > ?) AS revenue,
                   (SELECT COALESCE(SUM(y.amount), 0) FROM payouts y JOIN promos p ON p.id = y.promo_id
                     WHERE COALESCE(p.hidden_in_report, 0) = 0 AND y.created_at > ?) AS paid,
                   (SELECT COUNT(*) FROM sales s JOIN promos p ON p.id = s.promo_id
                     WHERE s.created_at >= ? AND s.created_at > ? AND COALESCE(s.is_reverted, 0) = 0 AND COALESCE(p.hidden_in_report, 0) = 0) AS sales_7d,
                   (SELECT COUNT(*) FROM sales s JOIN promos p ON p.id = s.promo_id
                     WHERE s.created_at >= ? AND s.created_at > ? AND COALESCE(s.is_reverted, 0) = 0 AND COALESCE(p.hidden_in_report, 0) = 0) AS sales_30d,
                   (SELECT COALESCE(SUM(amount), 0) FROM payments
                     WHERE status = 'CONFIRMED' AND created_at > ?) AS revenue_all
            """,
            (
                since,
                since,
                since,
                since,
                ts - 7 * DAY,
                since,
                ts - 30 * DAY,
                since,
                since,
            ),
        ) as cur:
            row = await cur.fetchone()
        return dict(row)

    async def top_promos(self, limit: int = 10) -> list[tuple[Promo, int, int]]:
        """Промокоды с наибольшим числом продаж: (промо, продажи, начислено)."""
        since = await self.get_report_since()
        async with self.conn.execute(
            """
            SELECT p.*,
                   COUNT(s.id)                 AS sales_total,
                   COALESCE(SUM(s.reward), 0)  AS earned
              FROM promos p
              LEFT JOIN sales s
                ON s.promo_id = p.id
               AND COALESCE(s.is_reverted, 0) = 0
               AND s.created_at > ?
             WHERE COALESCE(p.hidden_in_report, 0) = 0
             GROUP BY p.id
             ORDER BY sales_total DESC, p.code
             LIMIT ?
            """,
            (since, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [(Promo.from_row(row), row["sales_total"], row["earned"]) for row in rows]

    async def reset_report(self, created_by: int | None = None) -> tuple[int, int]:
        """Закрывает текущий отчёт выплатами и начинает новый 30-дневный период.

        Возвращает (сколько промо закрыто с ненулевым остатком, сумма закрытия).
        """
        ts = now_ts()
        closed = 0
        total = 0
        for promo in await self.list_promos():
            if promo.hidden_in_report:
                continue
            stats = await self.promo_stats(promo.id)
            if stats.balance > 0:
                await self.add_payout(
                    promo.id,
                    stats.balance,
                    comment="Сброс отчёта, новый период 30 дней",
                    created_by=created_by,
                    created_at=ts,
                )
                closed += 1
                total += stats.balance
        await self.set_setting(REPORT_SINCE_KEY, str(ts))
        return closed, total

    # --------------------------------------------------------------- settings

    async def get_setting(self, key: str) -> str | None:
        async with self.conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
        return row["value"] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        await self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self.conn.commit()

    async def get_report_since(self) -> int:
        raw = await self.get_setting(REPORT_SINCE_KEY)
        if not raw:
            return 0
        try:
            return int(raw)
        except ValueError:
            return 0

    # --------------------------------------------------------------- payments

    async def get_payment(self, transaction_id: str) -> PaymentRecord | None:
        async with self.conn.execute(
            "SELECT * FROM payments WHERE transaction_id = ?", (transaction_id,)
        ) as cur:
            row = await cur.fetchone()
        return PaymentRecord.from_row(row) if row else None

    async def get_payment_by_id(self, payment_id: int) -> PaymentRecord | None:
        async with self.conn.execute(
            "SELECT * FROM payments WHERE id = ?", (payment_id,)
        ) as cur:
            row = await cur.fetchone()
        return PaymentRecord.from_row(row) if row else None

    async def upsert_payment(
        self,
        *,
        transaction_id: str,
        amount: int,
        currency: str,
        status: str,
        payload: str | None,
        description: str | None,
        payment_method: str | None,
        promo_code: str | None,
        platega_created_at: str | None,
        created_at: int,
    ) -> PaymentRecord:
        existing = await self.get_payment(transaction_id)
        if existing is None:
            await self.conn.execute(
                """
                INSERT INTO payments (
                    transaction_id, amount, currency, status, payload, description,
                    payment_method, promo_code, created_at, platega_created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transaction_id,
                    amount,
                    currency,
                    status,
                    payload,
                    description,
                    payment_method,
                    promo_code,
                    created_at,
                    platega_created_at,
                ),
            )
        else:
            await self.conn.execute(
                """
                UPDATE payments
                   SET amount = ?, currency = ?, status = ?, payload = ?,
                       description = ?, payment_method = ?,
                       promo_code = COALESCE(?, promo_code),
                       platega_created_at = COALESCE(?, platega_created_at)
                 WHERE transaction_id = ?
                """,
                (
                    amount,
                    currency,
                    status,
                    payload,
                    description,
                    payment_method,
                    promo_code,
                    platega_created_at,
                    transaction_id,
                ),
            )
        await self.conn.commit()
        payment = await self.get_payment(transaction_id)
        assert payment is not None
        return payment

    async def mark_payment_processed(
        self,
        transaction_id: str,
        *,
        promo_id: int | None,
        sale_id: int | None,
        promo_code: str | None = None,
        status: str | None = None,
    ) -> None:
        fields = ["processed_at = ?", "promo_id = ?", "sale_id = ?"]
        values: list[object] = [now_ts(), promo_id, sale_id]
        if promo_code is not None:
            fields.append("promo_code = ?")
            values.append(promo_code)
        if status is not None:
            fields.append("status = ?")
            values.append(status)
        values.append(transaction_id)
        await self.conn.execute(
            f"UPDATE payments SET {', '.join(fields)} WHERE transaction_id = ?",
            values,
        )
        await self.conn.commit()

    async def list_unmatched_payments(self, limit: int = 20) -> list[PaymentRecord]:
        async with self.conn.execute(
            """
            SELECT * FROM payments
             WHERE status = 'CONFIRMED'
               AND sale_id IS NULL
               AND promo_id IS NULL
             ORDER BY created_at DESC, id DESC
             LIMIT ?
            """,
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [PaymentRecord.from_row(row) for row in rows]

    async def unmatched_count(self) -> int:
        async with self.conn.execute(
            """
            SELECT COUNT(*) AS n FROM payments
             WHERE status = 'CONFIRMED' AND sale_id IS NULL AND promo_id IS NULL
            """
        ) as cur:
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

