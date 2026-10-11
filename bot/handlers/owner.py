from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.config import Config
from bot.db import Database, Promo
from bot.keyboards import owner_back_kb, owner_menu_kb
from bot.texts import (
    NOT_BOUND,
    render_owner_stats,
    render_payouts_list,
    render_sales_list,
)

router = Router(name="owner")


async def build_owner_stats(
    db: Database, config: Config, user_id: int
) -> tuple[str, InlineKeyboardMarkup] | None:
    """Текст и клавиатура кабинета владельца. None — если промокодов нет."""
    promos = await db.list_promos_by_owner(user_id)
    if not promos:
        return None
    items = [(promo, await db.promo_stats(promo.id)) for promo in promos]
    return render_owner_stats(items, config.tz_offset_hours), owner_menu_kb(promos)


async def send_owner_stats(message: Message, db: Database, config: Config) -> bool:
    payload = await build_owner_stats(db, config, message.from_user.id)
    if payload is None:
        return False
    text, markup = payload
    await message.answer(text, reply_markup=markup)
    return True


async def _safe_edit(
    callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None
) -> None:
    """Повторное нажатие кнопки не должно падать на «message is not modified»."""
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        pass


async def _owner_promo(
    db: Database, callback: CallbackQuery, promo_id: int
) -> Promo | None:
    promo = await db.get_promo_by_id(promo_id)
    if promo is None or promo.owner_id != callback.from_user.id:
        await callback.answer("Промокод недоступен", show_alert=True)
        return None
    return promo


@router.callback_query(F.data == "own:refresh")
async def refresh_stats(callback: CallbackQuery, db: Database, config: Config) -> None:
    payload = await build_owner_stats(db, config, callback.from_user.id)
    if payload is None:
        await _safe_edit(callback, NOT_BOUND)
        await callback.answer()
        return
    text, markup = payload
    await _safe_edit(callback, text, markup)
    await callback.answer("Обновлено")


@router.callback_query(F.data.startswith("own:sales:"))
async def show_sales(callback: CallbackQuery, db: Database, config: Config) -> None:
    promo = await _owner_promo(db, callback, int(callback.data.split(":")[2]))
    if promo is None:
        return
    sales = await db.recent_sales(promo.id, limit=10)
    await _safe_edit(
        callback,
        render_sales_list(promo, sales, config.tz_offset_hours),
        owner_back_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("own:payouts:"))
async def show_payouts(callback: CallbackQuery, db: Database, config: Config) -> None:
    promo = await _owner_promo(db, callback, int(callback.data.split(":")[2]))
    if promo is None:
        return
    payouts = await db.recent_payouts(promo.id, limit=10)
    await _safe_edit(
        callback,
        render_payouts_list(promo, payouts, config.tz_offset_hours),
        owner_back_kb(),
    )
    await callback.answer()


@router.message(F.text)
async def fallback(message: Message, db: Database, config: Config) -> None:
    if not await send_owner_stats(message, db, config):
        await message.answer(NOT_BOUND)
