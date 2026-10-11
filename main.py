from __future__ import annotations

import asyncio
import logging
import sys
import os
import threading
import os
import threading
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is active!"

def run_flask():
    # Считываем порт, который сгенерировала AlwaysData, или 8100 по умолчанию
    port = int(os.environ.get("PORT", 8100))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

# Запуск в фоновом потоке
threading.Thread(target=run_flask, daemon=True).start()

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

from bot.config import ConfigError, load_config
from bot.db import Database
from bot.handlers import build_router
from bot.platega import PlategaClient
from bot.poller import PlategaPoller, notify_startup_status

logger = logging.getLogger("promo-bot")

OWNER_COMMANDS = [
    BotCommand(command="start", description="Моя статистика"),
    BotCommand(command="help", description="Как это работает"),
]

ADMIN_COMMANDS = [
    BotCommand(command="menu", description="Панель управления"),
    BotCommand(command="sale", description="Отметить покупку по промокоду"),
    BotCommand(command="payout", description="Отметить выплату"),
    BotCommand(command="addpromo", description="Создать промокод"),
    BotCommand(command="rate", description="Изменить ставку"),
    BotCommand(command="promos", description="Список промокодов"),
    BotCommand(command="stats", description="Общая статистика"),
    BotCommand(command="link", description="Ссылка для владельца"),
    BotCommand(command="bind", description="Привязать владельца по Telegram ID"),
    BotCommand(command="sync", description="Подтянуть оплаты из Platega"),
    BotCommand(command="cancel", description="Отменить действие"),
]


async def setup_commands(bot: Bot, admin_ids: frozenset[int]) -> None:
    await bot.set_my_commands(OWNER_COMMANDS, scope=BotCommandScopeDefault())
    for admin_id in admin_ids:
        try:
            await bot.set_my_commands(
                ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id)
            )
        except Exception as exc:  # администратор мог не запускать бота
            logger.warning("Не удалось задать команды для %s: %s", admin_id, exc)


async def run() -> None:
    config = load_config()

    db = Database(config.db_path)
    await db.connect()
    logger.info("База данных: %s", config.db_path)

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    client = None
    poller = None
    if config.platega_enabled:
        client = PlategaClient(
            config.platega_merchant_id,
            config.platega_secret,
            config.platega_base_url,
        )
        poller = PlategaPoller(client, db, bot, config)
        logger.info("Platega: опрос выгрузки каждые %s сек.", config.platega_poll_interval)
    else:
        logger.warning(
            "Platega выключена: задайте PLATEGA_MERCHANT_ID в .env "
            "(UUID из кабинета → Настройки)"
        )

    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher["db"] = db
    dispatcher["config"] = config
    dispatcher["poller"] = poller
    dispatcher.include_router(build_router())

    me = await bot.get_me()
    logger.info("Запуск бота @%s, администраторов: %d", me.username, len(config.admin_ids))
    await setup_commands(bot, config.admin_ids)
    await bot.delete_webhook(drop_pending_updates=True)
    await notify_startup_status(bot, config, client)

    poll_task = asyncio.create_task(poller.run_forever()) if poller else None
    try:
        await dispatcher.start_polling(bot)
    finally:
        if poll_task is not None:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
        await db.close()
        await bot.session.close()
        logger.info("Бот остановлен")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run())
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
