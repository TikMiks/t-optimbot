from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.deep_linking import create_start_link

from bot.config import Config
from bot.db import Database, Promo, PromoCodeExists
from bot.filters import IsAdmin
from bot.keyboards import (
    admin_menu_kb,
    attach_promo_kb,
    back_to_menu_kb,
    confirm_delete_kb,
    confirm_hide_kb,
    confirm_reset_kb,
    default_reward_kb,
    payout_amount_kb,
    payout_result_kb,
    promo_card_kb,
    promo_list_kb,
    promo_picker_kb,
    sale_amount_kb,
    sale_result_kb,
    unmatched_list_kb,
)
from bot.notifications import (
    notify_admins,
    notify_payout,
    notify_rate_change,
    notify_sale,
    notify_sale_cancelled,
)
from bot.texts import (
    render_admin_promo_card,
    render_bind_link,
    render_bind_success,
    render_global_stats,
    render_promo_list,
)
from bot.utils import (
    esc,
    fmt_dt,
    fmt_money,
    normalize_code,
    parse_money,
    user_label,
)

router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

MENU_TEXT = (
    "🛠 <b>Панель администратора</b>\n\n"
    "Покупки с сайта подхватываются из Platega сами. "
    "Вручную можно отметить продажу, выплату и привязать платёж без промокода.\n\n"
    "Подсказка: <code>/sale КОД</code>, <code>/payout КОД</code>, "
    "<code>/sync</code>. Полный список — /help"
)

SKIP_WORDS = {"-", "0", "нет", "skip", "пропустить", "/skip"}


class AddPromo(StatesGroup):
    code = State()
    reward = State()


class SaleFlow(StatesGroup):
    code = State()
    amount = State()


class PayoutFlow(StatesGroup):
    code = State()
    amount = State()


class RateFlow(StatesGroup):
    code = State()
    reward = State()


class BindIdFlow(StatesGroup):
    user_id = State()


# ------------------------------------------------------------------ хелперы


async def send_admin_menu(message: Message) -> None:
    await message.answer(MENU_TEXT, reply_markup=admin_menu_kb())


async def _edit(
    callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None
) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=markup)


async def _resolve_code(db: Database, raw: str) -> tuple[Promo | None, str | None]:
    try:
        code = normalize_code(raw)
    except ValueError as exc:
        return None, str(exc)
    promo = await db.get_promo_by_code(code)
    if promo is None:
        return None, f"Промокод <code>{esc(code)}</code> не найден."
    return promo, None


async def _show_promo_card(
    target: Message, db: Database, config: Config, promo: Promo
) -> None:
    stats = await db.promo_stats(promo.id)
    await target.answer(
        render_admin_promo_card(promo, stats, config.tz_offset_hours),
        reply_markup=promo_card_kb(promo),
    )


async def _open_picker(callback: CallbackQuery, db: Database, action: str, title: str) -> None:
    promos = await db.list_promos()
    if not promos:
        await _edit(
            callback,
            "Промокодов пока нет. Создайте первый кнопкой ➕ Новый промокод.",
            back_to_menu_kb(),
        )
        await callback.answer()
        return
    await _edit(callback, title, promo_picker_kb(promos, action))
    await callback.answer()


async def _bind_link(bot: Bot, db: Database, promo: Promo) -> str:
    token = promo.bind_token or await db.reset_bind_token(promo.id)
    return await create_start_link(bot, f"b_{token}")


def _parse_tg_id(raw: str) -> int | None:
    text = (raw or "").strip().replace(" ", "")
    if text.isdigit():
        value = int(text)
        if value > 0:
            return value
    return None


async def _lookup_tg_user(bot: Bot, user_id: int) -> tuple[str | None, str | None]:
    try:
        chat = await bot.get_chat(user_id)
    except TelegramAPIError:
        return None, None
    username = getattr(chat, "username", None)
    name = getattr(chat, "full_name", None) or getattr(chat, "first_name", None)
    return username, name


async def _bind_owner_by_id(
    target: Message,
    bot: Bot,
    db: Database,
    config: Config,
    promo: Promo,
    owner_id: int,
) -> None:
    username, name = await _lookup_tg_user(bot, owner_id)
    await db.bind_owner(promo.id, owner_id, username, name)
    bound = await db.get_promo_by_id(promo.id)
    delivered = False
    try:
        await bot.send_message(owner_id, render_bind_success(bound))
        delivered = True
    except TelegramAPIError:
        delivered = False
    lines = [
        f"✅ Промокод <code>{esc(promo.code)}</code> привязан к "
        f"{user_label(owner_id, username, name)}."
    ]
    if not delivered:
        lines.append(
            "\n⚠️ Человеку не ушло сообщение: пусть напишет боту /start, "
            "иначе кабинет и уведомления не откроются."
        )
    await target.answer("\n".join(lines))
    await _show_promo_card(target, db, config, bound)


async def _register_sale(
    target: Message,
    bot: Bot,
    db: Database,
    config: Config,
    promo: Promo,
    amount: int | None,
    admin_id: int,
) -> None:
    await db.add_sale(
        promo_id=promo.id,
        reward=promo.reward,
        amount=amount,
        created_by=admin_id,
    )
    stats = await db.promo_stats(promo.id)
    delivered = await notify_sale(bot, db, config, promo, promo.reward)

    lines = [
        f"✅ Покупка по <code>{esc(promo.code)}</code> записана.",
        f"Начислено владельцу: <b>{fmt_money(promo.reward)}</b>",
    ]
    if amount:
        lines.append(f"Сумма покупки: {fmt_money(amount)}")
    lines += [
        "",
        f"🧾 Всего покупок: <b>{stats.sales_total}</b>",
        f"🪙 К выплате: <b>{fmt_money(stats.balance)}</b>",
    ]
    if not promo.is_active:
        lines.append("\n⏸ Промокод приостановлен — продажа записана, проверьте это.")
    if promo.owner_id is None:
        lines.append("\n⚠️ Владелец не привязан — уведомление не отправлено.")
    elif not delivered:
        lines.append("\n⚠️ Не удалось доставить уведомление владельцу.")
    await target.answer("\n".join(lines), reply_markup=sale_result_kb(promo.id))


async def _register_payout(
    target: Message,
    bot: Bot,
    db: Database,
    config: Config,
    promo: Promo,
    amount: int,
    admin_id: int,
) -> None:
    await db.add_payout(promo_id=promo.id, amount=amount, created_by=admin_id)
    stats = await db.promo_stats(promo.id)
    delivered = await notify_payout(bot, db, config, promo, amount)

    lines = [
        f"💸 Выплата по <code>{esc(promo.code)}</code> записана.",
        f"Сумма: <b>{fmt_money(amount)}</b>",
        "",
        f"✅ Выплачено всего: {fmt_money(stats.paid)}",
        f"🪙 Остаток к выплате: <b>{fmt_money(stats.balance)}</b>",
    ]
    if stats.balance < 0:
        lines.append("\n⚠️ Выплачено больше, чем начислено — проверьте сумму.")
    if promo.owner_id is None:
        lines.append("\n⚠️ Владелец не привязан — уведомление не отправлено.")
    elif not delivered:
        lines.append("\n⚠️ Не удалось доставить уведомление владельцу.")
    await target.answer("\n".join(lines), reply_markup=payout_result_kb(promo.id))


async def _run_sync(target: Message, poller) -> None:
    if poller is None:
        await target.answer(
            "Platega ещё не подключена: в .env нет PLATEGA_MERCHANT_ID. "
            "Это UUID из кабинета my.platega.io → Настройки. "
            "Секрет уже сохранён локально."
        )
        return
    wait = await target.answer("🔄 Запрашиваю выгрузку Platega…")
    result = await poller.sync_once()
    text = f"✅ Синхронизация: {result.summary()}."
    if result.errors:
        text += "\n\n" + "\n".join(f"• {esc(err)}" for err in result.errors[:5])
    try:
        await wait.edit_text(text, reply_markup=back_to_menu_kb())
    except TelegramBadRequest:
        await target.answer(text, reply_markup=back_to_menu_kb())


async def _show_unmatched(callback: CallbackQuery, db: Database) -> None:
    items = await db.list_unmatched_payments()
    if not items:
        await _edit(
            callback,
            "Неразобранных платежей нет. Все CONFIRMED-оплаты либо уже "
            "привязаны к промокоду, либо ещё не приходили из Platega.",
            back_to_menu_kb(),
        )
        await callback.answer()
        return
    lines = ["❓ <b>Платежи без промокода</b>", ""]
    for payment in items:
        snippet = (payment.payload or payment.description or "—")[:80]
        lines.append(
            f"#{payment.id} · {fmt_money(payment.amount)} · "
            f"{esc(payment.transaction_id[:13])}…\n"
            f"   <code>{esc(snippet)}</code>"
        )
    await _edit(
        callback,
        "\n".join(lines),
        unmatched_list_kb([item.id for item in items]),
    )
    await callback.answer()


# ------------------------------------------------------------------- команды


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await send_admin_menu(message)


@router.message(Command("promos"))
async def cmd_promos(message: Message, db: Database) -> None:
    promos = await db.list_promos()
    items = [(promo, await db.promo_stats(promo.id)) for promo in promos]
    await message.answer(render_promo_list(items), reply_markup=promo_list_kb(promos))


@router.message(Command("sync"))
async def cmd_sync(message: Message, poller=None) -> None:
    await _run_sync(message, poller)


@router.message(Command("stats"))
async def cmd_stats(message: Message, db: Database, config: Config) -> None:
    data = await db.global_stats()
    top = await db.top_promos(limit=10)
    await message.answer(
        render_global_stats(
            data, top, config.tz_offset_hours, await db.get_report_since()
        ),
        reply_markup=back_to_menu_kb(),
    )


@router.message(Command("addpromo"))
async def cmd_addpromo(
    message: Message, command: CommandObject, bot: Bot, db: Database, config: Config
) -> None:
    args = (command.args or "").split()
    if not args:
        await message.answer(
            "Использование: <code>/addpromo КОД [ставка]</code>\n"
            "Например: <code>/addpromo NEON15 500</code>"
        )
        return
    try:
        code = normalize_code(args[0])
    except ValueError as exc:
        await message.answer(str(exc))
        return
    reward = config.default_reward
    if len(args) > 1:
        try:
            reward = parse_money(args[1])
        except ValueError as exc:
            await message.answer(f"Ставка: {exc}")
            return
    try:
        promo = await db.create_promo(code, reward)
    except PromoCodeExists:
        await message.answer(f"Промокод <code>{esc(code)}</code> уже существует.")
        return
    link = await _bind_link(bot, db, promo)
    await message.answer(render_bind_link(promo, link))
    await _show_promo_card(message, db, config, promo)


@router.message(Command("sale"))
async def cmd_sale(
    message: Message, command: CommandObject, bot: Bot, db: Database, config: Config
) -> None:
    args = (command.args or "").split()
    if not args:
        await message.answer("Использование: <code>/sale КОД [сумма покупки]</code>")
        return
    promo, error = await _resolve_code(db, args[0])
    if promo is None:
        await message.answer(error)
        return
    amount: int | None = None
    if len(args) > 1:
        try:
            amount = parse_money(args[1])
        except ValueError as exc:
            await message.answer(f"Сумма покупки: {exc}")
            return
    await _register_sale(message, bot, db, config, promo, amount, message.from_user.id)


@router.message(Command("payout"))
async def cmd_payout(
    message: Message, command: CommandObject, bot: Bot, db: Database, config: Config
) -> None:
    args = (command.args or "").split()
    if not args:
        await message.answer(
            "Использование: <code>/payout КОД [сумма]</code>\n"
            "Без суммы спишется весь текущий остаток."
        )
        return
    promo, error = await _resolve_code(db, args[0])
    if promo is None:
        await message.answer(error)
        return
    if len(args) > 1:
        try:
            amount = parse_money(args[1])
        except ValueError as exc:
            await message.answer(f"Сумма выплаты: {exc}")
            return
    else:
        amount = (await db.promo_stats(promo.id)).balance
    if amount <= 0:
        await message.answer(
            f"По <code>{esc(promo.code)}</code> нечего выплачивать: остаток "
            f"{fmt_money(amount)}."
        )
        return
    await _register_payout(message, bot, db, config, promo, amount, message.from_user.id)


@router.message(Command("rate"))
async def cmd_rate(
    message: Message, command: CommandObject, bot: Bot, db: Database, config: Config
) -> None:
    args = (command.args or "").split()
    if len(args) < 2:
        await message.answer("Использование: <code>/rate КОД ставка</code>")
        return
    promo, error = await _resolve_code(db, args[0])
    if promo is None:
        await message.answer(error)
        return
    try:
        reward = parse_money(args[1])
    except ValueError as exc:
        await message.answer(f"Ставка: {exc}")
        return
    await db.set_reward(promo.id, reward)
    await notify_rate_change(bot, promo, reward)
    updated = await db.get_promo_by_id(promo.id)
    await message.answer(
        f"⚙️ Ставка по <code>{esc(promo.code)}</code>: <b>{fmt_money(reward)}</b> за покупку."
    )
    await _show_promo_card(message, db, config, updated)


@router.message(Command("link"))
async def cmd_link(
    message: Message, command: CommandObject, bot: Bot, db: Database
) -> None:
    args = (command.args or "").split()
    if not args:
        await message.answer("Использование: <code>/link КОД</code>")
        return
    promo, error = await _resolve_code(db, args[0])
    if promo is None:
        await message.answer(error)
        return
    link = await _bind_link(bot, db, promo)
    await message.answer(render_bind_link(promo, link))


@router.message(Command("bind"))
async def cmd_bind(
    message: Message, command: CommandObject, bot: Bot, db: Database, config: Config
) -> None:
    args = (command.args or "").split()
    if len(args) < 2:
        await message.answer("Использование: <code>/bind КОД TELEGRAM_ID</code>")
        return
    promo, error = await _resolve_code(db, args[0])
    if promo is None:
        await message.answer(error)
        return
    owner_id = _parse_tg_id(args[1])
    if owner_id is None:
        await message.answer("Telegram ID должен быть положительным числом.")
        return
    await _bind_owner_by_id(message, bot, db, config, promo, owner_id)


# ----------------------------------------------------------------- навигация


@router.callback_query(F.data == "adm:home")
async def cb_home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _edit(callback, MENU_TEXT, admin_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "adm:promos")
async def cb_promos(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    promos = await db.list_promos()
    items = [(promo, await db.promo_stats(promo.id)) for promo in promos]
    await _edit(callback, render_promo_list(items), promo_list_kb(promos))
    await callback.answer()


@router.callback_query(F.data == "adm:stats")
async def cb_stats(callback: CallbackQuery, db: Database, config: Config) -> None:
    data = await db.global_stats()
    top = await db.top_promos(limit=10)
    await _edit(
        callback,
        render_global_stats(
            data, top, config.tz_offset_hours, await db.get_report_since()
        ),
        back_to_menu_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:reset")
async def cb_reset(callback: CallbackQuery, db: Database, config: Config) -> None:
    since = await db.get_report_since()
    data = await db.global_stats()
    since_line = f"Текущий отчёт с {fmt_dt(since, config.tz_offset_hours)}.\n" if since else ""
    await _edit(
        callback,
        "🧹 <b>Сбросить отчёт и начать заново?</b>\n\n"
        f"{since_line}"
        f"Сейчас в отчёте {data['sales_total']} покупок, "
        f"к выплате {fmt_money(data['earned'] - data['paid'])}.\n\n"
        "Остатки закроются как выплата, счётчики обнулятся, "
        "и новый отчёт пойдёт следующие 30 дней. История в базе сохранится.",
        confirm_reset_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:resetok")
async def cb_reset_ok(callback: CallbackQuery, db: Database, config: Config) -> None:
    closed, total = await db.reset_report(created_by=callback.from_user.id)
    since = await db.get_report_since()
    text = (
        "🧹 <b>Новый отчёт начат</b>\n\n"
        f"С {fmt_dt(since, config.tz_offset_hours)} статистика считается заново.\n"
        f"Закрыто промокодов с остатком: {closed} на {fmt_money(total)}.\n"
        "Следующие оплаты снова пойдут в «к выплате»."
    )
    await _edit(callback, text, back_to_menu_kb())
    await notify_admins(callback.bot, config, text)
    await callback.answer("Отчёт сброшен")


@router.callback_query(F.data.startswith("adm:promo:"))
async def cb_promo_card(callback: CallbackQuery, db: Database, config: Config) -> None:
    promo = await db.get_promo_by_id(int(callback.data.split(":")[2]))
    if promo is None:
        await callback.answer("Промокод удалён", show_alert=True)
        return
    stats = await db.promo_stats(promo.id)
    await _edit(
        callback,
        render_admin_promo_card(promo, stats, config.tz_offset_hours),
        promo_card_kb(promo),
    )
    await callback.answer()


# ------------------------------------------------------------- новый промокод


@router.callback_query(F.data == "adm:add")
async def cb_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddPromo.code)
    await _edit(
        callback,
        "Введите новый промокод (латиница, цифры, дефис — например <code>NEON15</code>).",
        back_to_menu_kb(),
    )
    await callback.answer()


@router.message(AddPromo.code)
async def add_promo_code(message: Message, db: Database, config: Config, state: FSMContext) -> None:
    try:
        code = normalize_code(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    if await db.get_promo_by_code(code):
        await message.answer(
            f"Промокод <code>{esc(code)}</code> уже существует. Введите другой."
        )
        return
    await state.update_data(code=code)
    await state.set_state(AddPromo.reward)
    await message.answer(
        f"Промокод: <code>{esc(code)}</code>\n\n"
        "Сколько начислять владельцу за одну покупку? Введите сумму в рублях "
        "или используйте значение по умолчанию.",
        reply_markup=default_reward_kb(config.default_reward),
    )


async def _create_promo(
    target: Message, bot: Bot, db: Database, config: Config, state: FSMContext, reward: int
) -> None:
    data = await state.get_data()
    await state.clear()
    code = data.get("code")
    if not code:
        await target.answer("Что-то пошло не так, начните заново: /menu")
        return
    try:
        promo = await db.create_promo(code, reward)
    except PromoCodeExists:
        await target.answer(f"Промокод <code>{esc(code)}</code> уже существует.")
        return
    link = await _bind_link(bot, db, promo)
    await target.answer(render_bind_link(promo, link))
    await _show_promo_card(target, db, config, promo)


@router.callback_query(AddPromo.reward, F.data == "adm:use_default_reward")
async def cb_default_reward(
    callback: CallbackQuery, bot: Bot, db: Database, config: Config, state: FSMContext
) -> None:
    await _create_promo(callback.message, bot, db, config, state, config.default_reward)
    await callback.answer()


@router.message(AddPromo.reward)
async def add_promo_reward(
    message: Message, bot: Bot, db: Database, config: Config, state: FSMContext
) -> None:
    try:
        reward = parse_money(message.text or "")
    except ValueError as exc:
        await message.answer(f"Ставка: {exc}. Введите сумму в рублях, например 500.")
        return
    await _create_promo(message, bot, db, config, state, reward)


# ---------------------------------------------------------------- продажи


@router.callback_query(F.data == "adm:sale_pick")
async def cb_sale_pick(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    await _open_picker(
        callback, db, "sale", "🧾 По какому промокоду была покупка?"
    )


@router.callback_query(F.data == "adm:sale_manual")
async def cb_sale_manual(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SaleFlow.code)
    await _edit(callback, "Введите промокод покупателя.", back_to_menu_kb())
    await callback.answer()


@router.message(SaleFlow.code)
async def sale_code(message: Message, db: Database, state: FSMContext) -> None:
    promo, error = await _resolve_code(db, message.text or "")
    if promo is None:
        await message.answer(error)
        return
    await state.update_data(promo_id=promo.id)
    await state.set_state(SaleFlow.amount)
    await message.answer(
        f"Промокод <code>{esc(promo.code)}</code>, начисление "
        f"<b>{fmt_money(promo.reward)}</b>.\n\n"
        "Введите сумму покупки в рублях — или пропустите этот шаг.",
        reply_markup=sale_amount_kb(),
    )


@router.callback_query(F.data.startswith("adm:sale:"))
async def cb_sale_promo(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    promo = await db.get_promo_by_id(int(callback.data.split(":")[2]))
    if promo is None:
        await callback.answer("Промокод удалён", show_alert=True)
        return
    await state.set_state(SaleFlow.amount)
    await state.update_data(promo_id=promo.id)
    await _edit(
        callback,
        f"Промокод <code>{esc(promo.code)}</code>, начисление "
        f"<b>{fmt_money(promo.reward)}</b>.\n\n"
        "Введите сумму покупки в рублях — или пропустите этот шаг.",
        sale_amount_kb(),
    )
    await callback.answer()


async def _finish_sale(
    target: Message,
    bot: Bot,
    db: Database,
    config: Config,
    state: FSMContext,
    amount: int | None,
    admin_id: int,
) -> None:
    data = await state.get_data()
    await state.clear()
    promo = await db.get_promo_by_id(data.get("promo_id", 0))
    if promo is None:
        await target.answer("Промокод не найден, начните заново: /menu")
        return
    await _register_sale(target, bot, db, config, promo, amount, admin_id)


@router.callback_query(SaleFlow.amount, F.data == "adm:sale_skip")
async def cb_sale_skip(
    callback: CallbackQuery, bot: Bot, db: Database, config: Config, state: FSMContext
) -> None:
    await _finish_sale(
        callback.message, bot, db, config, state, None, callback.from_user.id
    )
    await callback.answer()


@router.message(SaleFlow.amount)
async def sale_amount(
    message: Message, bot: Bot, db: Database, config: Config, state: FSMContext
) -> None:
    raw = (message.text or "").strip()
    if raw.lower() in SKIP_WORDS:
        await _finish_sale(message, bot, db, config, state, None, message.from_user.id)
        return
    try:
        amount = parse_money(raw)
    except ValueError as exc:
        await message.answer(
            f"Сумма покупки: {exc}. Введите число в рублях или нажмите «Без суммы покупки»."
        )
        return
    await _finish_sale(message, bot, db, config, state, amount, message.from_user.id)


@router.callback_query(F.data.startswith("adm:undo:"))
async def cb_undo_sale(
    callback: CallbackQuery, bot: Bot, db: Database, config: Config
) -> None:
    promo = await db.get_promo_by_id(int(callback.data.split(":")[2]))
    if promo is None:
        await callback.answer("Промокод удалён", show_alert=True)
        return
    removed = await db.delete_last_sale(promo.id)
    if removed is None:
        await callback.answer("Продаж по этому промокоду нет", show_alert=True)
        return
    stats = await db.promo_stats(promo.id)
    await notify_sale_cancelled(bot, promo, removed.reward, stats.balance)
    await _edit(
        callback,
        f"↩️ Последняя продажа по <code>{esc(promo.code)}</code> удалена "
        f"(−{fmt_money(removed.reward)}).\n\n"
        f"🧾 Покупок: <b>{stats.sales_total}</b>\n"
        f"🪙 К выплате: <b>{fmt_money(stats.balance)}</b>",
        payout_result_kb(promo.id),
    )
    await callback.answer("Продажа отменена")


# ----------------------------------------------------------------- выплаты


@router.callback_query(F.data == "adm:payout_pick")
async def cb_payout_pick(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    await _open_picker(
        callback, db, "payout", "💸 По какому промокоду вы произвели выплату?"
    )


@router.callback_query(F.data == "adm:payout_manual")
async def cb_payout_manual(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PayoutFlow.code)
    await _edit(callback, "Введите промокод, по которому была выплата.", back_to_menu_kb())
    await callback.answer()


async def _payout_prompt(
    db: Database, promo: Promo, state: FSMContext
) -> tuple[str, InlineKeyboardMarkup]:
    stats = await db.promo_stats(promo.id)
    await state.set_state(PayoutFlow.amount)
    await state.update_data(promo_id=promo.id)
    hint = (
        "Введите сумму выплаты в рублях или нажмите кнопку ниже."
        if stats.balance > 0
        else "Начислений к выплате нет. Если выплачиваете авансом — введите сумму вручную."
    )
    text = (
        f"💸 Выплата по <code>{esc(promo.code)}</code>\n\n"
        f"🪙 Текущий остаток к выплате: <b>{fmt_money(stats.balance)}</b>\n"
        f"✅ Выплачено ранее: {fmt_money(stats.paid)}\n\n"
        f"{hint}"
    )
    return text, payout_amount_kb(promo.id, stats.balance)


@router.message(PayoutFlow.code)
async def payout_code(message: Message, db: Database, state: FSMContext) -> None:
    promo, error = await _resolve_code(db, message.text or "")
    if promo is None:
        await message.answer(error)
        return
    text, markup = await _payout_prompt(db, promo, state)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("adm:payout:"))
async def cb_payout_promo(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    promo = await db.get_promo_by_id(int(callback.data.split(":")[2]))
    if promo is None:
        await callback.answer("Промокод удалён", show_alert=True)
        return
    text, markup = await _payout_prompt(db, promo, state)
    await _edit(callback, text, markup)
    await callback.answer()


@router.callback_query(F.data.startswith("adm:payout_all:"))
async def cb_payout_all(
    callback: CallbackQuery, bot: Bot, db: Database, config: Config, state: FSMContext
) -> None:
    await state.clear()
    promo = await db.get_promo_by_id(int(callback.data.split(":")[2]))
    if promo is None:
        await callback.answer("Промокод удалён", show_alert=True)
        return
    balance = (await db.promo_stats(promo.id)).balance
    if balance <= 0:
        await callback.answer("Нечего выплачивать", show_alert=True)
        return
    await _register_payout(
        callback.message, bot, db, config, promo, balance, callback.from_user.id
    )
    await callback.answer()


@router.message(PayoutFlow.amount)
async def payout_amount(
    message: Message, bot: Bot, db: Database, config: Config, state: FSMContext
) -> None:
    try:
        amount = parse_money(message.text or "")
    except ValueError as exc:
        await message.answer(f"Сумма выплаты: {exc}. Введите число в рублях.")
        return
    if amount <= 0:
        await message.answer("Сумма выплаты должна быть больше нуля.")
        return
    data = await state.get_data()
    await state.clear()
    promo = await db.get_promo_by_id(data.get("promo_id", 0))
    if promo is None:
        await message.answer("Промокод не найден, начните заново: /menu")
        return
    await _register_payout(message, bot, db, config, promo, amount, message.from_user.id)


# ------------------------------------------------------------------- ставка


@router.callback_query(F.data == "adm:rate_pick")
async def cb_rate_pick(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    await _open_picker(callback, db, "rate", "⚙️ У какого промокода меняем ставку?")


@router.callback_query(F.data == "adm:rate_manual")
async def cb_rate_manual(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RateFlow.code)
    await _edit(callback, "Введите промокод.", back_to_menu_kb())
    await callback.answer()


@router.message(RateFlow.code)
async def rate_code(message: Message, db: Database, state: FSMContext) -> None:
    promo, error = await _resolve_code(db, message.text or "")
    if promo is None:
        await message.answer(error)
        return
    await state.update_data(promo_id=promo.id)
    await state.set_state(RateFlow.reward)
    await message.answer(
        f"Текущая ставка <code>{esc(promo.code)}</code>: "
        f"<b>{fmt_money(promo.reward)}</b>.\nВведите новую сумму в рублях.",
        reply_markup=back_to_menu_kb(),
    )


@router.callback_query(F.data.startswith("adm:rate:"))
async def cb_rate_promo(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    promo = await db.get_promo_by_id(int(callback.data.split(":")[2]))
    if promo is None:
        await callback.answer("Промокод удалён", show_alert=True)
        return
    await state.set_state(RateFlow.reward)
    await state.update_data(promo_id=promo.id)
    await _edit(
        callback,
        f"Текущая ставка <code>{esc(promo.code)}</code>: "
        f"<b>{fmt_money(promo.reward)}</b>.\nВведите новую сумму в рублях.",
        back_to_menu_kb(),
    )
    await callback.answer()


@router.message(RateFlow.reward)
async def rate_reward(
    message: Message, bot: Bot, db: Database, config: Config, state: FSMContext
) -> None:
    try:
        reward = parse_money(message.text or "")
    except ValueError as exc:
        await message.answer(f"Ставка: {exc}. Введите число в рублях, например 500.")
        return
    data = await state.get_data()
    await state.clear()
    promo = await db.get_promo_by_id(data.get("promo_id", 0))
    if promo is None:
        await message.answer("Промокод не найден, начните заново: /menu")
        return
    await db.set_reward(promo.id, reward)
    await notify_rate_change(bot, promo, reward)
    updated = await db.get_promo_by_id(promo.id)
    await message.answer(
        f"⚙️ Новая ставка по <code>{esc(promo.code)}</code>: <b>{fmt_money(reward)}</b>."
    )
    await _show_promo_card(message, db, config, updated)


# --------------------------------------------------- владелец и жизненный цикл


@router.callback_query(F.data.startswith("adm:bindid:"))
async def cb_bind_id(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    promo = await db.get_promo_by_id(int(callback.data.split(":")[2]))
    if promo is None:
        await callback.answer("Промокод удалён", show_alert=True)
        return
    await state.set_state(BindIdFlow.user_id)
    await state.update_data(promo_id=promo.id)
    await _edit(
        callback,
        f"Введите Telegram ID владельца для <code>{esc(promo.code)}</code>.\n"
        "Узнать ID можно командой /id в этом боте (пусть владелец напишет её) "
        "или у @userinfobot.",
        back_to_menu_kb(),
    )
    await callback.answer()


@router.message(BindIdFlow.user_id)
async def bind_id_entered(
    message: Message, bot: Bot, db: Database, config: Config, state: FSMContext
) -> None:
    owner_id = _parse_tg_id(message.text or "")
    if owner_id is None:
        await message.answer("Нужен числовой Telegram ID, например <code>6134902274</code>.")
        return
    data = await state.get_data()
    await state.clear()
    promo = await db.get_promo_by_id(data.get("promo_id", 0))
    if promo is None:
        await message.answer("Промокод не найден, начните заново: /menu")
        return
    await _bind_owner_by_id(message, bot, db, config, promo, owner_id)


@router.callback_query(F.data.startswith("adm:link:"))
async def cb_link(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    promo = await db.get_promo_by_id(int(callback.data.split(":")[2]))
    if promo is None:
        await callback.answer("Промокод удалён", show_alert=True)
        return
    link = await _bind_link(bot, db, promo)
    await _edit(callback, render_bind_link(promo, link), promo_card_kb(promo))
    await callback.answer()


@router.callback_query(F.data.startswith("adm:unbind:"))
async def cb_unbind(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    promo = await db.get_promo_by_id(int(callback.data.split(":")[2]))
    if promo is None:
        await callback.answer("Промокод удалён", show_alert=True)
        return
    await db.unbind_owner(promo.id)
    updated = await db.get_promo_by_id(promo.id)
    link = await _bind_link(bot, db, updated)
    await _edit(
        callback,
        f"🔓 Владелец промокода <code>{esc(promo.code)}</code> отвязан. "
        f"Статистика и выплаты сохранены.\n\n{render_bind_link(updated, link)}",
        promo_card_kb(updated),
    )
    await callback.answer("Владелец отвязан")


@router.callback_query(F.data.startswith("adm:toggle:"))
async def cb_toggle(callback: CallbackQuery, db: Database, config: Config) -> None:
    promo = await db.get_promo_by_id(int(callback.data.split(":")[2]))
    if promo is None:
        await callback.answer("Промокод удалён", show_alert=True)
        return
    await db.set_active(promo.id, not promo.is_active)
    updated = await db.get_promo_by_id(promo.id)
    stats = await db.promo_stats(updated.id)
    await _edit(
        callback,
        render_admin_promo_card(updated, stats, config.tz_offset_hours),
        promo_card_kb(updated),
    )
    await callback.answer("Промокод включён" if updated.is_active else "Промокод выключен")


@router.callback_query(F.data.startswith("adm:delok:"))
async def cb_delete_confirmed(callback: CallbackQuery, db: Database) -> None:
    promo = await db.get_promo_by_id(int(callback.data.split(":")[2]))
    if promo is None:
        await callback.answer("Промокод уже удалён", show_alert=True)
        return
    await db.delete_promo(promo.id)
    await _edit(
        callback,
        f"🗑 Промокод <code>{esc(promo.code)}</code> удалён вместе с его продажами "
        "и выплатами.",
        back_to_menu_kb(),
    )
    await callback.answer("Удалено")


@router.callback_query(F.data == "adm:sync")
async def cb_sync(callback: CallbackQuery, poller=None) -> None:
    await callback.answer("Синхронизирую…")
    await _run_sync(callback.message, poller)


@router.callback_query(F.data == "adm:unmatched")
async def cb_unmatched(callback: CallbackQuery, db: Database, config: Config) -> None:
    await _show_unmatched(callback, db)


@router.callback_query(F.data.startswith("adm:attach:"))
async def cb_attach(callback: CallbackQuery, db: Database) -> None:
    payment = await db.get_payment_by_id(int(callback.data.split(":")[2]))
    if payment is None or payment.sale_id:
        await callback.answer("Платёж уже обработан", show_alert=True)
        return
    promos = await db.list_promos()
    if not promos:
        await callback.answer("Сначала создайте промокод", show_alert=True)
        return
    snippet = (payment.payload or payment.description or "—")[:120]
    await _edit(
        callback,
        f"Привязать платёж <code>{esc(payment.transaction_id)}</code>\n"
        f"Сумма: {fmt_money(payment.amount)}\n"
        f"Данные: <code>{esc(snippet)}</code>\n\n"
        "Выберите промокод:",
        attach_promo_kb(payment.id, promos),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:attok:"))
async def cb_attach_ok(
    callback: CallbackQuery, bot: Bot, db: Database, config: Config
) -> None:
    _, _, payment_id, promo_id = callback.data.split(":")
    payment = await db.get_payment_by_id(int(payment_id))
    promo = await db.get_promo_by_id(int(promo_id))
    if payment is None or promo is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    if payment.sale_id:
        await callback.answer("Этот платёж уже привязан", show_alert=True)
        return
    sale_id = await db.add_sale(
        promo_id=promo.id,
        reward=promo.reward,
        amount=payment.amount or None,
        note=f"Platega {payment.transaction_id}",
        created_by=callback.from_user.id,
        external_id=payment.transaction_id,
        created_at=payment.created_at,
    )
    await db.mark_payment_processed(
        payment.transaction_id,
        promo_id=promo.id,
        sale_id=sale_id,
        promo_code=promo.code,
        status="CONFIRMED",
    )
    delivered = await notify_sale(bot, db, config, promo, promo.reward)
    extra = ""
    if promo.owner_id is None:
        extra = "\n⚠️ Владелец не привязан."
    elif not delivered:
        extra = "\n⚠️ Не удалось уведомить владельца."
    stats = await db.promo_stats(promo.id)
    await _edit(
        callback,
        f"✅ Платёж привязан к <code>{esc(promo.code)}</code>.\n"
        f"Начислено: <b>{fmt_money(promo.reward)}</b>\n"
        f"🪙 К выплате: <b>{fmt_money(stats.balance)}</b>{extra}",
        promo_card_kb(promo),
    )
    await callback.answer("Готово")


@router.callback_query(F.data.startswith("adm:hideok:"))
async def cb_hide_ok(callback: CallbackQuery, db: Database, config: Config) -> None:
    promo = await db.get_promo_by_id(int(callback.data.split(":")[2]))
    if promo is None:
        await callback.answer("Промокод удалён", show_alert=True)
        return
    await db.set_hidden_in_report(promo.id, True)
    updated = await db.get_promo_by_id(promo.id)
    stats = await db.promo_stats(updated.id)
    await _edit(
        callback,
        render_admin_promo_card(updated, stats, config.tz_offset_hours)
        + "\n\n🙈 Этот промокод больше не входит в общую статистику и долг к выплате. "
        "Покупки по нему по-прежнему пишутся, владельцу уходят уведомления. "
        "Вернуть в отчёт можно кнопкой на карточке.",
        promo_card_kb(updated),
    )
    await callback.answer("Убран из отчёта")


@router.callback_query(F.data.startswith("adm:hide:"))
async def cb_hide(callback: CallbackQuery, db: Database, config: Config) -> None:
    promo = await db.get_promo_by_id(int(callback.data.split(":")[2]))
    if promo is None:
        await callback.answer("Промокод удалён", show_alert=True)
        return
    if promo.hidden_in_report:
        await db.set_hidden_in_report(promo.id, False)
        updated = await db.get_promo_by_id(promo.id)
        stats = await db.promo_stats(updated.id)
        await _edit(
            callback,
            render_admin_promo_card(updated, stats, config.tz_offset_hours)
            + "\n\n👁 Промокод снова в общем отчёте.",
            promo_card_kb(updated),
        )
        await callback.answer("Вернули в отчёт")
        return
    stats = await db.promo_stats(promo.id)
    await _edit(
        callback,
        f"🙈 Убрать <code>{esc(promo.code)}</code> из текущего отчёта?\n\n"
        f"Сейчас по нему {stats.sales_total} покупок, "
        f"к выплате {fmt_money(stats.balance)}.\n"
        "Из общей статистики и долга он пропадёт. "
        "Сам промокод, владелец и история останутся.",
        confirm_hide_kb(promo.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:del:"))
async def cb_delete(callback: CallbackQuery, db: Database) -> None:
    promo = await db.get_promo_by_id(int(callback.data.split(":")[2]))
    if promo is None:
        await callback.answer("Промокод удалён", show_alert=True)
        return
    stats = await db.promo_stats(promo.id)
    await _edit(
        callback,
        f"🗑 Удалить промокод <code>{esc(promo.code)}</code>?\n\n"
        f"Будут удалены {stats.sales_total} продаж и вся история выплат. "
        "Действие необратимо.\n\n"
        "Если нужно лишь приостановить начисления — используйте «⏸ Выключить».",
        confirm_delete_kb(promo.id),
    )
    await callback.answer()


@router.message(StateFilter(None), F.text)
async def admin_fallback(message: Message) -> None:
    await send_admin_menu(message)
