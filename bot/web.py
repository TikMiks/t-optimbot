"""HTTP-заглушка для хостингов вроде Render: порт для проверки здоровья и самопинг."""

from __future__ import annotations

import asyncio
import logging

import aiohttp
from aiohttp import web

log = logging.getLogger(__name__)

# Render усыпляет бесплатный сервис после 15 минут без входящих запросов.
KEEP_ALIVE_INTERVAL = 10 * 60


async def _health(_: web.Request) -> web.Response:
    return web.Response(text="ok")


async def start_health_server(port: int) -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/health", _health)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("HTTP-проверка здоровья слушает порт %s", port)
    return runner


async def keep_alive(url: str, interval: int = KEEP_ALIVE_INTERVAL) -> None:
    target = url.rstrip("/") + "/health"
    log.info("Самопинг %s каждые %s сек.", target, interval)
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            await asyncio.sleep(interval)
            try:
                async with session.get(target) as response:
                    if response.status != 200:
                        log.warning("Самопинг: ответ %s", response.status)
            except Exception as exc:  # сеть могла моргнуть — попробуем в следующий раз
                log.warning("Самопинг не прошёл: %s", exc)
