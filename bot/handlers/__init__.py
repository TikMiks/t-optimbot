from aiogram import Router

from bot.handlers.admin import router as admin_router
from bot.handlers.common import router as common_router
from bot.handlers.owner import router as owner_router


def build_router() -> Router:
    """Порядок важен: сначала общие команды, затем админские, затем кабинет владельца."""
    router = Router(name="root")
    router.include_router(common_router)
    router.include_router(admin_router)
    router.include_router(owner_router)
    return router


__all__ = ["build_router"]
