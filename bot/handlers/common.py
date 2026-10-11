from __future__ import annotations

import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.config import Config
from bot.db import Database
from bot.handlers.admin import send_admin_menu
from bot.handlers.owner import send_owner_stats
from bot.texts import HELP_OWNER, NOT_BOUND, render_bind_success
from bot.utils import esc, user_label

logger = logging.getLogger(__name__)
router = Router(name="common")

BIND_PREFIX = "b_"


async def _announce_bind(bot: Bot, config: Config, code: str, message: Message) -> None:
    user = message.from_user
    text = (
        f"🔗 Промокод <code>{esc(code)}</code> привязан к "
        f"{user_label(user.id, user.username, user.full_name)}"
    )
    for admin_id in config.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except TelegramAPIError as exc:
            logger.warning("Не удалось уведомить админа %s: %s", admin_id, exc)


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    bot: Bot,
    db: Database,
    config: Config,
    state: FSMContext,
) -> None:
    await state.clear()
    user = message.from_user
    payload = (command.args or "").strip()

    if payload.startswith(BIND_PREFIX):
        token = payload[len(BIND_PREFIX) :]
        promo = await db.get_promo_by_token(token)
        if promo is None:
            await message.answer(
                "⚠️ Ссылка недействительна или уже использована. "
                "Запросите новую у администратора."
            )
        else:
            await db.bind_owner(promo.id, user.id, user.username, user.full_name)
            bound = await db.get_promo_by_id(promo.id)
            await message.answer(render_bind_success(bound))
            await _announce_bind(bot, config, bound.code, message)

    await db.refresh_owner_profile(user.id, user.username, user.full_name)

    if config.is_admin(user.id):
        await send_admin_menu(message)
        return

    if not await send_owner_stats(message, db, config):
        await message.answer(NOT_BOUND)


@router.message(Command("help"))
async def cmd_help(message: Message, config: Config) -> None:
    if config.is_admin(message.from_user.id):
        await message.answer(
            "<b>Команды администратора</b>\n\n"
            "/menu — панель управления\n"
            "/sale КОД [сумма] — отметить покупку по промокоду\n"
            "/payout КОД [сумма] — отметить выплату (без суммы — весь остаток)\n"
            "/addpromo КОД [ставка] — создать промокод\n"
            "/rate КОД ставка — изменить начисление за покупку\n"
            "/promos — список промокодов\n"
            "/stats — общая статистика\n"
            "/link КОД — ссылка для привязки владельца\n"
            "/bind КОД ID — привязать владельца по Telegram ID\n"
            "/sync — подтянуть оплаты из Platega\n"
            "/cancel — прервать текущее действие\n\n"
            "Кнопка «🧹 Сбросить отчёт» начинает новый 30-дневный период."
        )
    else:
        await message.answer(HELP_OWNER)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("Нечего отменять.")
        return
    await state.clear()
    await message.answer("Действие отменено.")


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")
