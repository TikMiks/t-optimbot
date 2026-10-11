from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from bot.config import Config
from bot.db import Database, Promo
from bot.texts import (
    render_admin_sale_notification,
    render_chargeback_notification,
    render_payout_notification,
    render_sale_notification,
)
from bot.utils import esc, fmt_money

logger = logging.getLogger(__name__)


async def _send(bot: Bot, chat_id: int, text: str) -> bool:
    try:
        await bot.send_message(chat_id, text)
    except TelegramAPIError as exc:
        logger.warning("Не удалось отправить уведомление %s: %s", chat_id, exc)
        return False
    return True


async def notify_owner(bot: Bot, promo: Promo, text: str) -> bool:
    if promo.owner_id is None:
        return False
    return await _send(bot, promo.owner_id, text)


async def notify_sale(
    bot: Bot, db: Database, config: Config, promo: Promo, reward: int
) -> bool:
    """Сообщает владельцу о новой покупке. Возвращает False, если доставить не удалось."""
    if promo.owner_id is None:
        return False
    stats = await db.promo_stats(promo.id)
    text = render_sale_notification(promo, stats, reward, config.tz_offset_hours)
    return await _send(bot, promo.owner_id, text)


async def notify_admin_sale(
    bot: Bot,
    config: Config,
    promo: Promo,
    *,
    buyer: str | None,
    when_ts: int | None,
    amount: int | None,
    reward: int,
) -> None:
    text = render_admin_sale_notification(
        promo, buyer, when_ts, amount, reward, config.tz_offset_hours
    )
    await notify_admins(bot, config, text)


async def notify_payout(
    bot: Bot, db: Database, config: Config, promo: Promo, amount: int
) -> bool:
    if promo.owner_id is None:
        return False
    stats = await db.promo_stats(promo.id)
    text = render_payout_notification(promo, amount, stats, config.tz_offset_hours)
    return await _send(bot, promo.owner_id, text)


async def notify_rate_change(bot: Bot, promo: Promo, reward: int) -> bool:
    return await notify_owner(
        bot,
        promo,
        f"⚙️ Ставка по промокоду <code>{esc(promo.code)}</code> изменена: "
        f"теперь <b>{fmt_money(reward)}</b> за покупку.",
    )


async def notify_sale_cancelled(
    bot: Bot, promo: Promo, reward: int, balance: int
) -> bool:
    return await notify_owner(
        bot,
        promo,
        f"↩️ Покупка по промокоду <code>{esc(promo.code)}</code> отменена "
        f"администратором: списано {fmt_money(reward)}.\n"
        f"🪙 К выплате: <b>{fmt_money(balance)}</b>",
    )


async def notify_chargeback(bot: Bot, promo: Promo, reward: int, balance: int) -> bool:
    return await notify_owner(
        bot, promo, render_chargeback_notification(promo, reward, balance)
    )


async def notify_admins(bot: Bot, config: Config, text: str) -> None:
    for admin_id in config.admin_ids:
        await _send(bot, admin_id, text)
