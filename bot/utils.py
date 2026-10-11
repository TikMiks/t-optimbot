from __future__ import annotations

import html
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CODE_RE = re.compile(r"^[\w\-]{2,32}$", re.UNICODE)

_MONEY_NOISE = ("₽", "руб.", "руб", "r", "р.", "р")


def now_ts() -> int:
    return int(time.time())


def new_token() -> str:
    return secrets.token_urlsafe(9)


def normalize_code(raw: str) -> str:
    """Приводит промокод к каноническому виду или бросает ValueError."""
    code = raw.strip().lstrip("#").upper()
    if not CODE_RE.match(code):
        raise ValueError(
            "Промокод должен быть от 2 до 32 символов: буквы, цифры, дефис или подчёркивание."
        )
    return code


def parse_money(raw: str) -> int:
    """Разбирает сумму в рублях (`500`, `1 250,50`, `500₽`) в копейки."""
    text = raw.strip().lower().replace("\u00a0", "").replace(" ", "")
    for noise in _MONEY_NOISE:
        if text.endswith(noise):
            text = text[: -len(noise)]
            break
    text = text.replace(",", ".")
    if not text:
        raise ValueError("Пустая сумма")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("Не похоже на сумму") from exc
    if value < 0:
        raise ValueError("Сумма не может быть отрицательной")
    if value > Decimal("100000000"):
        raise ValueError("Слишком большая сумма")
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fmt_money(kopecks: int | None) -> str:
    if kopecks is None:
        return "—"
    rubles, kop = divmod(abs(int(kopecks)), 100)
    sign = "−" if kopecks < 0 else ""
    body = f"{rubles:,}".replace(",", " ")
    if kop:
        body = f"{body},{kop:02d}"
    return f"{sign}{body} ₽"


def fmt_dt(ts: int | None, tz_offset_hours: int) -> str:
    if not ts:
        return "—"
    tz = timezone(timedelta(hours=tz_offset_hours))
    return datetime.fromtimestamp(ts, tz).strftime("%d.%m.%Y %H:%M")


def fmt_time(ts: int | None, tz_offset_hours: int) -> str:
    if not ts:
        return "—"
    tz = timezone(timedelta(hours=tz_offset_hours))
    return datetime.fromtimestamp(ts, tz).strftime("%H:%M:%S")


def esc(value: object) -> str:
    return html.escape(str(value), quote=False)


def user_label(user_id: int | None, username: str | None, name: str | None) -> str:
    """Человекочитаемая подпись пользователя для админских экранов."""
    if user_id is None:
        return "не привязан"
    parts = []
    if name:
        parts.append(esc(name))
    if username:
        parts.append(f"@{esc(username)}")
    parts.append(f"<code>{user_id}</code>")
    return " · ".join(parts)
