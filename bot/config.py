from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from bot.utils import parse_money

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    db_path: Path
    database_url: str
    """Строка подключения Postgres. Если пусто — используется SQLite из db_path."""
    default_reward: int
    """Начисление за одну продажу в копейках."""
    tz_offset_hours: int
    platega_merchant_id: str
    platega_secret: str
    platega_base_url: str
    platega_poll_interval: int
    platega_lookback_hours: int

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.admin_ids

    @property
    def platega_enabled(self) -> bool:
        return bool(self.platega_merchant_id and self.platega_secret)


def _parse_admin_ids(raw: str) -> frozenset[int]:
    ids = {int(chunk) for chunk in re.split(r"[,;\s]+", raw.strip()) if chunk}
    if not ids:
        raise ConfigError(
            "ADMIN_IDS пуст. Укажите хотя бы один Telegram ID администратора в .env"
        )
    return frozenset(ids)


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError("BOT_TOKEN не задан. Скопируйте .env.example в .env и заполните его.")

    try:
        admin_ids = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))
    except ValueError as exc:
        raise ConfigError("ADMIN_IDS должен содержать только числовые Telegram ID") from exc

    db_path = Path(os.getenv("DB_PATH", "data/bot.db"))
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path

    try:
        default_reward = parse_money(os.getenv("DEFAULT_REWARD", "500"))
    except ValueError as exc:
        raise ConfigError("DEFAULT_REWARD должен быть неотрицательным числом рублей") from exc

    try:
        tz_offset = int(os.getenv("TZ_OFFSET_HOURS", "3"))
    except ValueError as exc:
        raise ConfigError("TZ_OFFSET_HOURS должен быть целым числом") from exc

    try:
        poll_interval = int(os.getenv("PLATEGA_POLL_INTERVAL", "60"))
        lookback_hours = int(os.getenv("PLATEGA_LOOKBACK_HOURS", "48"))
    except ValueError as exc:
        raise ConfigError(
            "PLATEGA_POLL_INTERVAL и PLATEGA_LOOKBACK_HOURS должны быть целыми числами"
        ) from exc
    if poll_interval < 15:
        raise ConfigError("PLATEGA_POLL_INTERVAL не может быть меньше 15 секунд")
    if lookback_hours < 1:
        raise ConfigError("PLATEGA_LOOKBACK_HOURS должен быть не меньше 1")

    return Config(
        bot_token=token,
        admin_ids=admin_ids,
        db_path=db_path,
        database_url=os.getenv("DATABASE_URL", "").strip(),
        default_reward=default_reward,
        tz_offset_hours=tz_offset,
        platega_merchant_id=os.getenv("PLATEGA_MERCHANT_ID", "").strip(),
        platega_secret=os.getenv("PLATEGA_SECRET", "").strip(),
        platega_base_url=os.getenv("PLATEGA_BASE_URL", "https://app.platega.io").rstrip("/"),
        platega_poll_interval=poll_interval,
        platega_lookback_hours=lookback_hours,
    )
