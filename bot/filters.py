from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from bot.config import Config


class IsAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery, config: Config) -> bool:
        return config.is_admin(event.from_user.id if event.from_user else None)
