"""Фоновый опрос выгрузки транзакций Platega."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import aiosqlite
from aiogram import Bot

from bot.config import Config
from bot.db import Database, PaymentRecord, Promo
from bot.notifications import notify_admin_sale, notify_admins, notify_chargeback, notify_sale
from bot.platega import (
    INTERESTING_STATUSES,
    STATUS_CHARGEBACKED,
    STATUS_CONFIRMED,
    PlategaClient,
    PlategaError,
    PlategaTransaction,
    extract_buyer_username,
    extract_promo_hint,
    match_known_promo,
)
from bot.utils import fmt_money, now_ts

log = logging.getLogger(__name__)

CURSOR_KEY = "platega_last_from"


@dataclass
class SyncResult:
    fetched: int = 0
    credited: int = 0
    reverted: int = 0
    unmatched: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"просмотрено {self.fetched}",
            f"начислено {self.credited}",
            f"возвратов {self.reverted}",
            f"без промо {self.unmatched}",
        ]
        if self.skipped:
            parts.append(f"пропущено {self.skipped}")
        if self.errors:
            parts.append(f"ошибок {len(self.errors)}")
        return ", ".join(parts)


class PlategaPoller:
    def __init__(self, client: PlategaClient, db: Database, bot: Bot, config: Config) -> None:
        self.client = client
        self.db = db
        self.bot = bot
        self.config = config
        self._lock = asyncio.Lock()

    async def run_forever(self) -> None:
        interval = self.config.platega_poll_interval
        log.info("Опрос Platega каждые %s сек.", interval)
        while True:
            try:
                result = await self.sync_once()
                if result.credited or result.reverted or result.unmatched or result.errors:
                    log.info("Синхронизация Platega: %s", result.summary())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Сбой цикла опроса Platega")
            await asyncio.sleep(interval)

    async def sync_once(self) -> SyncResult:
        async with self._lock:
            return await self._sync()

    async def _sync(self) -> SyncResult:
        result = SyncResult()
        now = datetime.now(timezone.utc)
        date_from = await self._window_start(now)
        try:
            rows = await self.client.export_transactions(date_from, now)
        except PlategaError as exc:
            result.errors.append(str(exc))
            log.warning("Выгрузка Platega не удалась: %s", exc)
            return result

        result.fetched = len(rows)
        known = [promo.code for promo in await self.db.list_promos()]
        for tx in rows:
            try:
                await self._handle(tx, known, result)
            except Exception as exc:  # одна битая запись не валит весь проход
                log.exception("Не обработали транзакцию %s", tx.transaction_id)
                result.errors.append(f"{tx.transaction_id}: {exc}")

        await self.db.set_setting(CURSOR_KEY, now.isoformat())
        return result

    async def _window_start(self, now: datetime) -> datetime:
        lookback = now - timedelta(hours=self.config.platega_lookback_hours)
        raw = await self.db.get_setting(CURSOR_KEY)
        if not raw:
            return lookback
        try:
            cursor = datetime.fromisoformat(raw)
            if cursor.tzinfo is None:
                cursor = cursor.replace(tzinfo=timezone.utc)
        except ValueError:
            return lookback
        # небольшой нахлёст, чтобы не потерять границу интервала
        return max(lookback, cursor - timedelta(minutes=30))

    async def _handle(
        self, tx: PlategaTransaction, known_codes: list[str], result: SyncResult
    ) -> None:
        if tx.status not in INTERESTING_STATUSES:
            result.skipped += 1
            return

        hint = extract_promo_hint(tx.payload, tx.description)
        if hint is None:
            hint = match_known_promo([tx.payload, tx.description], known_codes)

        existing = await self.db.get_payment(tx.transaction_id)
        payment = await self.db.upsert_payment(
            transaction_id=tx.transaction_id,
            amount=tx.amount_kopecks,
            currency=tx.currency,
            status=tx.status,
            payload=tx.payload or None,
            description=tx.description or None,
            payment_method=tx.payment_method or None,
            promo_code=hint,
            platega_created_at=tx.created_at_raw or None,
            created_at=tx.created_at or now_ts(),
        )

        if tx.status == STATUS_CONFIRMED:
            await self._credit(payment, hint, existing, result)
            return
        if tx.status == STATUS_CHARGEBACKED:
            await self._revert(payment, existing, result)

    async def _credit(
        self,
        payment: PaymentRecord,
        hint: str | None,
        previous: PaymentRecord | None,
        result: SyncResult,
    ) -> None:
        if previous and previous.sale_id:
            result.skipped += 1
            return
        if payment.sale_id:
            result.skipped += 1
            return

        promo = await self._resolve_promo(hint)
        if promo is None:
            if not hint:
                result.skipped += 1
                return
            result.unmatched += 1
            if previous is None or previous.status != STATUS_CONFIRMED or previous.promo_id:
                await self._notify_unmatched(payment, hint)
            return

        try:
            sale_id = await self.db.add_sale(
                promo_id=promo.id,
                reward=promo.reward,
                amount=payment.amount or None,
                note=f"Platega {payment.transaction_id}",
                external_id=payment.transaction_id,
                created_at=payment.created_at,
            )
        except aiosqlite.IntegrityError:
            result.skipped += 1
            return
        await self.db.mark_payment_processed(
            payment.transaction_id,
            promo_id=promo.id,
            sale_id=sale_id,
            promo_code=promo.code,
            status=STATUS_CONFIRMED,
        )
        fresh = await self.db.get_promo_by_id(promo.id)
        assert fresh is not None
        await notify_sale(self.bot, self.db, self.config, fresh, fresh.reward)
        await notify_admin_sale(
            self.bot,
            self.config,
            fresh,
            buyer=extract_buyer_username(payment.payload, payment.description),
            when_ts=payment.created_at,
            amount=payment.amount,
            reward=fresh.reward,
        )
        result.credited += 1
        log.info(
            "Начислили %s по промо %s, транзакция %s",
            fmt_money(fresh.reward),
            fresh.code,
            payment.transaction_id,
        )

    async def _revert(
        self,
        payment: PaymentRecord,
        previous: PaymentRecord | None,
        result: SyncResult,
    ) -> None:
        sale_id = payment.sale_id or (previous.sale_id if previous else None)
        if previous and previous.status == STATUS_CHARGEBACKED and previous.sale_id is None:
            result.skipped += 1
            return

        sale = await self.db.revert_sale(sale_id) if sale_id else None
        promo = None
        if payment.promo_id:
            promo = await self.db.get_promo_by_id(payment.promo_id)
        elif sale is None and payment.promo_code:
            promo = await self.db.get_promo_by_code(payment.promo_code)

        await self.db.mark_payment_processed(
            payment.transaction_id,
            promo_id=promo.id if promo else payment.promo_id,
            sale_id=None,
            status=STATUS_CHARGEBACKED,
        )
        if sale is None and previous is None:
            result.skipped += 1
            return
        result.reverted += 1
        reward = sale.reward if sale else (promo.reward if promo else 0)
        if promo is not None:
            stats = await self.db.promo_stats(promo.id)
            await notify_chargeback(self.bot, promo, reward, stats.balance)
        await notify_admins(
            self.bot,
            self.config,
            (
                f"↩️ Возврат Platega {payment.transaction_id}\n"
                f"Промо: <code>{promo.code if promo else '—'}</code>\n"
                f"Списано: {fmt_money(reward)}"
            ),
        )

    async def _resolve_promo(self, hint: str | None) -> Promo | None:
        if not hint:
            return None
        return await self.db.get_promo_by_code(hint)

    async def _notify_unmatched(self, payment: PaymentRecord, hint: str | None) -> None:
        snippet = (payment.payload or payment.description or "").strip()
        if len(snippet) > 180:
            snippet = snippet[:177] + "…"
        text = (
            "❓ <b>Платёж Platega без известного промокода</b>\n\n"
            f"ID: <code>{payment.transaction_id}</code>\n"
            f"Сумма: {fmt_money(payment.amount)}\n"
            f"Подсказка: <code>{hint or 'нет'}</code>\n"
        )
        if snippet:
            text += f"Данные: <code>{snippet}</code>\n"
        text += "\nПривяжите его вручную: панель → Неразобранные платежи."
        await notify_admins(self.bot, self.config, text)


async def notify_startup_status(bot: Bot, config: Config, client: PlategaClient | None) -> None:
    if client is None:
        await notify_admins(
            bot,
            config,
            "⚠️ Platega не запущена: в .env нет PLATEGA_MERCHANT_ID. "
            "Секрет уже есть, осталось вписать UUID мерчанта из кабинета → Настройки.",
        )
        return
    try:
        balances = await client.get_balances()
    except PlategaError as exc:
        await notify_admins(bot, config, f"⚠️ Platega не ответила при старте: {exc}")
        return
    except Exception as exc:
        log.warning("Проверка баланса Platega не удалась: %s", exc)
        return
    parts = []
    for item in balances:
        amount = item.get("amount")
        currency = item.get("currency") or ""
        if amount is not None:
            parts.append(f"{amount} {currency}".strip())
    extra = f" Баланс: {', '.join(parts)}." if parts else ""
    await notify_admins(
        bot,
        config,
        f"✅ Связь с Platega есть. Бот сам подхватит оплаты по промокодам.{extra}",
    )
