from __future__ import annotations

from bot.db import PayoutRecord, Promo, PromoStats, SaleRecord
from bot.utils import esc, fmt_dt, fmt_money, fmt_time, now_ts, user_label

HELP_OWNER = (
    "Это кабинет владельца промокода <b>t-optim.cc</b>.\n\n"
    "• Каждая покупка по вашему промокоду попадает в статистику, "
    "а вам сразу приходит уведомление с суммой начисления.\n"
    "• Кнопка «🔄 Обновить статистику» показывает актуальные цифры.\n"
    "• В блоке «Последняя выплата» видно, когда и сколько вам выплатили."
)

NOT_BOUND = (
    "👋 Привет!\n\n"
    "Этот бот считает покупки по партнёрским промокодам <b>t-optim.cc</b> "
    "и показывает владельцу промокода его продажи и доход.\n\n"
    "Ваш Telegram пока не привязан ни к одному промокоду. "
    "Попросите администратора прислать персональную ссылку для привязки."
)


def promo_block(promo: Promo, stats: PromoStats, tz: int) -> str:
    """Карточка одного промокода для кабинета владельца."""
    lines = [
        f"🎟 Промокод <code>{esc(promo.code)}</code>"
        + ("" if promo.is_active else " <i>(приостановлен)</i>"),
        f"Ставка: <b>{fmt_money(promo.reward)}</b> за покупку",
        "",
        f"🧾 Покупок всего: <b>{stats.sales_total}</b>",
        f"   • за 7 дней: {stats.sales_7d}",
        f"   • за 30 дней: {stats.sales_30d}",
        f"💰 Начислено всего: <b>{fmt_money(stats.earned)}</b>",
        f"✅ Выплачено: {fmt_money(stats.paid)}",
        f"🪙 К выплате: <b>{fmt_money(stats.balance)}</b>",
        "",
    ]
    if stats.last_sale_at:
        lines.append(f"Последняя покупка: {fmt_dt(stats.last_sale_at, tz)}")
    else:
        lines.append("Покупок по промокоду пока не было.")

    if stats.last_payout_at:
        lines.append(
            f"Последняя выплата: {fmt_dt(stats.last_payout_at, tz)} — "
            f"<b>{fmt_money(stats.last_payout_amount)}</b>"
        )
    else:
        lines.append("Выплат пока не было.")
    return "\n".join(lines)


def render_owner_stats(items: list[tuple[Promo, PromoStats]], tz: int) -> str:
    header = "📊 <b>Ваша статистика</b>"
    if len(items) > 1:
        total_sales = sum(stats.sales_total for _, stats in items)
        total_balance = sum(stats.balance for _, stats in items)
        total_earned = sum(stats.earned for _, stats in items)
        header += (
            f"\n\nПо всем промокодам: <b>{total_sales}</b> покупок, "
            f"начислено <b>{fmt_money(total_earned)}</b>, "
            f"к выплате <b>{fmt_money(total_balance)}</b>"
        )
    blocks = [promo_block(promo, stats, tz) for promo, stats in items]
    footer = f"<i>Обновлено в {fmt_time(now_ts(), tz)}</i>"
    return "\n\n".join([header, *blocks, footer])


def render_sales_list(promo: Promo, sales: list[SaleRecord], tz: int) -> str:
    if not sales:
        return (
            f"🧾 Покупок по промокоду <code>{esc(promo.code)}</code> пока нет."
        )
    lines = [f"🧾 <b>Последние покупки по</b> <code>{esc(promo.code)}</code>", ""]
    for sale in sales:
        extra = f" · чек {fmt_money(sale.amount)}" if sale.amount else ""
        note = f"\n   <i>{esc(sale.note)}</i>" if sale.note else ""
        lines.append(
            f"• {fmt_dt(sale.created_at, tz)} — +{fmt_money(sale.reward)}{extra}{note}"
        )
    return "\n".join(lines)


def render_payouts_list(promo: Promo, payouts: list[PayoutRecord], tz: int) -> str:
    if not payouts:
        return f"💸 Выплат по промокоду <code>{esc(promo.code)}</code> пока не было."
    lines = [f"💸 <b>История выплат по</b> <code>{esc(promo.code)}</code>", ""]
    for payout in payouts:
        comment = f"\n   <i>{esc(payout.comment)}</i>" if payout.comment else ""
        lines.append(
            f"• {fmt_dt(payout.created_at, tz)} — <b>{fmt_money(payout.amount)}</b>{comment}"
        )
    return "\n".join(lines)


def render_admin_sale_notification(
    promo: Promo,
    buyer: str | None,
    when_ts: int | None,
    amount: int | None,
    reward: int,
    tz: int,
) -> str:
    buyer_line = f"@{esc(buyer)}" if buyer else "не указан"
    lines = [
        "✅ <b>Оплата прошла</b>",
        "",
        f"Покупатель: {buyer_line}",
        f"Промокод: <code>{esc(promo.code)}</code>",
        f"Когда: {fmt_dt(when_ts, tz)}",
    ]
    if amount:
        lines.append(f"Чек: {fmt_money(amount)}")
    lines.append(f"Начислено партнёру: <b>+{fmt_money(reward)}</b>")
    return "\n".join(lines)


def render_sale_notification(promo: Promo, stats: PromoStats, reward: int, tz: int) -> str:
    return (
        f"🎉 <b>Новая покупка по вашему промокоду</b> <code>{esc(promo.code)}</code>!\n\n"
        f"Вам начислено <b>+{fmt_money(reward)}</b>\n\n"
        f"🧾 Покупок всего: <b>{stats.sales_total}</b>\n"
        f"🪙 К выплате: <b>{fmt_money(stats.balance)}</b>\n"
        f"<i>{fmt_dt(stats.last_sale_at, tz)}</i>"
    )


def render_chargeback_notification(promo: Promo, reward: int, balance: int) -> str:
    return (
        f"↩️ <b>Возврат платежа</b> по промокоду <code>{esc(promo.code)}</code>\n\n"
        f"Списано начисление: <b>{fmt_money(reward)}</b>\n"
        f"🪙 К выплате: <b>{fmt_money(balance)}</b>"
    )


def render_payout_notification(
    promo: Promo, amount: int, stats: PromoStats, tz: int
) -> str:
    return (
        f"💸 <b>Выплата произведена</b>\n\n"
        f"Промокод: <code>{esc(promo.code)}</code>\n"
        f"Сумма выплаты: <b>{fmt_money(amount)}</b>\n"
        f"Дата: {fmt_dt(stats.last_payout_at, tz)}\n\n"
        f"✅ Выплачено всего: {fmt_money(stats.paid)}\n"
        f"🪙 Остаток к выплате: <b>{fmt_money(stats.balance)}</b>"
    )


def render_bind_success(promo: Promo) -> str:
    return (
        f"✅ Промокод <code>{esc(promo.code)}</code> привязан к вашему аккаунту.\n\n"
        f"Начисление: <b>{fmt_money(promo.reward)}</b> за каждую покупку.\n"
        "Теперь вы будете получать уведомление о каждой продаже."
    )


# --------------------------------------------------------------------- админ


def render_admin_promo_card(promo: Promo, stats: PromoStats, tz: int) -> str:
    status = "активен" if promo.is_active else "приостановлен"
    lines = [
        f"🎟 <b>{esc(promo.code)}</b> — {status}"
        + (" · 🙈 не в отчёте" if promo.hidden_in_report else ""),
        f"Ставка: <b>{fmt_money(promo.reward)}</b> за покупку",
        f"Владелец: {user_label(promo.owner_id, promo.owner_username, promo.owner_name)}",
        "",
        f"🧾 Покупок: <b>{stats.sales_total}</b> "
        f"(7 дн. — {stats.sales_7d}, 30 дн. — {stats.sales_30d})",
        f"💰 Начислено: <b>{fmt_money(stats.earned)}</b>",
        f"✅ Выплачено: {fmt_money(stats.paid)}",
        f"🪙 К выплате: <b>{fmt_money(stats.balance)}</b>",
    ]
    if stats.revenue:
        lines.append(f"🛒 Сумма покупок с промокода: {fmt_money(stats.revenue)}")
    lines += [
        "",
        f"Последняя покупка: {fmt_dt(stats.last_sale_at, tz)}",
        f"Последняя выплата: {fmt_dt(stats.last_payout_at, tz)}"
        + (f" — {fmt_money(stats.last_payout_amount)}" if stats.last_payout_at else ""),
        f"Создан: {fmt_dt(promo.created_at, tz)}",
    ]
    return "\n".join(lines)


def render_global_stats(
    data: dict[str, int],
    top: list[tuple[Promo, int, int]],
    tz: int,
    report_since: int | None = None,
) -> str:
    paid = data["paid"]
    earned = data["earned"]
    lines = [
        "📊 <b>Общая статистика</b>",
        *(
            [f"<i>Текущий отчёт с {fmt_dt(report_since, tz)}</i>", ""]
            if report_since
            else []
        ),
        f"🎟 Промокодов: <b>{data['promos_total']}</b> "
        f"(с владельцем — {data['promos_bound']})",
        f"🧾 Покупок всего: <b>{data['sales_total']}</b>",
        f"   • за 7 дней: {data['sales_7d']}",
        f"   • за 30 дней: {data['sales_30d']}",
        f"💰 Начислено владельцам: <b>{fmt_money(earned)}</b>",
        f"✅ Выплачено: {fmt_money(paid)}",
        f"🪙 Долг к выплате: <b>{fmt_money(earned - paid)}</b>",
        f"🛒 Сумма покупок с промокодов: <b>{fmt_money(data.get('revenue') or 0)}</b>",
        f"🛒 Сумма покупок всего: <b>{fmt_money(data.get('revenue_all') or 0)}</b>",
    ]

    ranked = [item for item in top if item[1] > 0]
    if ranked:
        lines += ["", "<b>Топ промокодов:</b>"]
        for index, (promo, sales_total, promo_earned) in enumerate(ranked, start=1):
            lines.append(
                f"{index}. <code>{esc(promo.code)}</code> — {sales_total} шт., "
                f"{fmt_money(promo_earned)}"
            )
    lines += ["", f"<i>Обновлено в {fmt_time(now_ts(), tz)}</i>"]
    return "\n".join(lines)


def render_promo_list(items: list[tuple[Promo, PromoStats]]) -> str:
    if not items:
        return "Промокодов пока нет. Создайте первый кнопкой ниже."
    lines = ["📋 <b>Промокоды</b>", ""]
    for promo, stats in items:
        mark = "" if promo.is_active else "⏸ "
        hide = "🙈 " if promo.hidden_in_report else ""
        owner = "✅" if promo.owner_id else "⚠️ без владельца"
        extra = " · не в отчёте" if promo.hidden_in_report else ""
        lines.append(
            f"{hide}{mark}<code>{esc(promo.code)}</code> — {stats.sales_total} шт. · "
            f"к выплате {fmt_money(stats.balance)} · {owner}{extra}"
        )
    lines += ["", "Нажмите на промокод, чтобы открыть карточку."]
    return "\n".join(lines)


def render_bind_link(promo: Promo, link: str) -> str:
    return (
        f"🔗 Ссылка для привязки владельца промокода <code>{esc(promo.code)}</code>:\n\n"
        f"{esc(link)}\n\n"
        "Отправьте её владельцу. После перехода по ссылке он автоматически получит "
        "доступ к статистике. Ссылка одноразовая.\n\n"
        "Можно привязать и без ссылки: кнопка «🆔 Привязать по ID» "
        "или команда <code>/bind КОД 123456789</code>."
    )
