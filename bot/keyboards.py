from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db import Promo
from bot.utils import fmt_money

PICKER_LIMIT = 40

_MENU_BUTTON = InlineKeyboardButton(text="⬅️ В меню", callback_data="adm:home")


def admin_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🧾 Отметить продажу", callback_data="adm:sale_pick")
    kb.button(text="💸 Отметить выплату", callback_data="adm:payout_pick")
    kb.button(text="➕ Новый промокод", callback_data="adm:add")
    kb.button(text="📋 Промокоды", callback_data="adm:promos")
    kb.button(text="⚙️ Изменить ставку", callback_data="adm:rate_pick")
    kb.button(text="📊 Общая статистика", callback_data="adm:stats")
    kb.button(text="🔄 Синхронизировать Platega", callback_data="adm:sync")
    kb.button(text="❓ Неразобранные платежи", callback_data="adm:unmatched")
    kb.button(text="🧹 Сбросить отчёт (30 дней)", callback_data="adm:reset")
    kb.adjust(1, 1, 2, 2, 1, 1, 1)
    return kb.as_markup()


def promo_picker_kb(promos: list[Promo], action: str) -> InlineKeyboardMarkup:
    """Список промокодов для выбора. `action` — часть callback: adm:<action>:<id>."""
    kb = InlineKeyboardBuilder()
    for promo in promos[:PICKER_LIMIT]:
        mark = "" if promo.is_active else "⏸ "
        owner = "" if promo.owner_id else " ⚠️"
        kb.button(
            text=f"{mark}{promo.code}{owner}",
            callback_data=f"adm:{action}:{promo.id}",
        )
    kb.adjust(2)
    kb.row(
        InlineKeyboardButton(
            text="✍️ Ввести код вручную", callback_data=f"adm:{action}_manual"
        )
    )
    kb.row(_MENU_BUTTON)
    return kb.as_markup()


def promo_list_kb(promos: list[Promo]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for promo in promos[:PICKER_LIMIT]:
        mark = "" if promo.is_active else "⏸ "
        hide = "🙈 " if promo.hidden_in_report else ""
        kb.button(
            text=f"{hide}{mark}{promo.code}", callback_data=f"adm:promo:{promo.id}"
        )
    kb.adjust(2)
    kb.row(InlineKeyboardButton(text="➕ Новый промокод", callback_data="adm:add"))
    kb.row(_MENU_BUTTON)
    return kb.as_markup()


def promo_card_kb(promo: Promo) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🧾 Продажа", callback_data=f"adm:sale:{promo.id}")
    kb.button(text="💸 Выплата", callback_data=f"adm:payout:{promo.id}")
    kb.button(text="⚙️ Ставка", callback_data=f"adm:rate:{promo.id}")
    kb.button(text="↩️ Отменить последнюю продажу", callback_data=f"adm:undo:{promo.id}")
    kb.button(text="🔗 Ссылка для владельца", callback_data=f"adm:link:{promo.id}")
    kb.button(text="🆔 Привязать по ID", callback_data=f"adm:bindid:{promo.id}")
    if promo.owner_id:
        kb.button(text="🔓 Отвязать владельца", callback_data=f"adm:unbind:{promo.id}")
    kb.button(
        text="⏸ Выключить" if promo.is_active else "▶️ Включить",
        callback_data=f"adm:toggle:{promo.id}",
    )
    kb.button(
        text="🙈 Убрать из отчёта"
        if not promo.hidden_in_report
        else "👁 Вернуть в отчёт",
        callback_data=f"adm:hide:{promo.id}",
    )
    kb.button(text="🗑 Удалить", callback_data=f"adm:del:{promo.id}")
    kb.button(text="⬅️ К списку", callback_data="adm:promos")
    kb.adjust(2, 1, 1, 2, 2, 2)
    return kb.as_markup()


def confirm_reset_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🧹 Да, начать новый отчёт", callback_data="adm:resetok")
    kb.button(text="⬅️ Отмена", callback_data="adm:home")
    kb.adjust(1)
    return kb.as_markup()


def confirm_hide_kb(promo_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🙈 Да, убрать из отчёта", callback_data=f"adm:hideok:{promo_id}")
    kb.button(text="⬅️ Отмена", callback_data=f"adm:promo:{promo_id}")
    kb.adjust(1)
    return kb.as_markup()


def confirm_delete_kb(promo_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Да, удалить", callback_data=f"adm:delok:{promo_id}")
    kb.button(text="⬅️ Отмена", callback_data=f"adm:promo:{promo_id}")
    kb.adjust(1)
    return kb.as_markup()


def payout_amount_kb(promo_id: int, balance: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if balance > 0:
        kb.button(
            text=f"✅ Выплатил всё — {fmt_money(balance)}",
            callback_data=f"adm:payout_all:{promo_id}",
        )
    kb.button(text="⬅️ Отмена", callback_data="adm:home")
    kb.adjust(1)
    return kb.as_markup()


def sale_amount_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⏭ Без суммы покупки", callback_data="adm:sale_skip")
    kb.button(text="⬅️ Отмена", callback_data="adm:home")
    kb.adjust(1)
    return kb.as_markup()


def default_reward_kb(default_reward: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text=f"💰 По умолчанию — {fmt_money(default_reward)}",
        callback_data="adm:use_default_reward",
    )
    kb.button(text="⬅️ Отмена", callback_data="adm:home")
    kb.adjust(1)
    return kb.as_markup()


def sale_result_kb(promo_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="↩️ Отменить эту продажу", callback_data=f"adm:undo:{promo_id}")
    kb.button(text="🧾 Ещё продажа", callback_data=f"adm:sale:{promo_id}")
    kb.button(text="🎟 Карточка промокода", callback_data=f"adm:promo:{promo_id}")
    kb.button(text="⬅️ В меню", callback_data="adm:home")
    kb.adjust(1, 2, 1)
    return kb.as_markup()


def payout_result_kb(promo_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🎟 Карточка промокода", callback_data=f"adm:promo:{promo_id}")
    kb.button(text="⬅️ В меню", callback_data="adm:home")
    kb.adjust(1)
    return kb.as_markup()


def unmatched_list_kb(payment_ids: list[int]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for payment_id in payment_ids:
        kb.button(text=f"Привязать #{payment_id}", callback_data=f"adm:attach:{payment_id}")
    kb.adjust(1)
    kb.row(_MENU_BUTTON)
    return kb.as_markup()


def attach_promo_kb(payment_id: int, promos: list[Promo]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for promo in promos[:PICKER_LIMIT]:
        kb.button(text=promo.code, callback_data=f"adm:attok:{payment_id}:{promo.id}")
    kb.adjust(2)
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:unmatched"))
    return kb.as_markup()


def back_to_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_MENU_BUTTON]])


# ----------------------------------------------------------------- владелец


def owner_menu_kb(promos: list[Promo]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🔄 Обновить статистику", callback_data="own:refresh")
    )
    if len(promos) == 1:
        promo_id = promos[0].id
        kb.row(
            InlineKeyboardButton(
                text="🧾 Последние покупки", callback_data=f"own:sales:{promo_id}"
            ),
            InlineKeyboardButton(
                text="💸 История выплат", callback_data=f"own:payouts:{promo_id}"
            ),
        )
    else:
        for promo in promos[:PICKER_LIMIT]:
            kb.row(
                InlineKeyboardButton(
                    text=f"🧾 {promo.code}", callback_data=f"own:sales:{promo.id}"
                ),
                InlineKeyboardButton(
                    text=f"💸 {promo.code}", callback_data=f"own:payouts:{promo.id}"
                ),
            )
    return kb.as_markup()


def owner_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ К статистике", callback_data="own:refresh")]
        ]
    )
