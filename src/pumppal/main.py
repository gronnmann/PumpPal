from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import uvicorn
from fastapi import FastAPI
from loguru import logger

from .agent import build_agent
from .bot import build_bot
from .hevy.client import HevyClient
from .logging_setup import setup_logging
from .settings import Settings, get_settings
from .state import get_state
from .webhook import router as webhook_router


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()

    state = get_state()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info("PumpPal starting up...")
        hevy_client = HevyClient(settings.hevy_api_key)
        model_name = build_agent(settings)
        tg_app: Any = build_bot(settings, hevy_client, state, model_name)

        # Store in app.state for access by webhook handler
        app.state.hevy_client = hevy_client
        app.state.tg_app = tg_app
        app.state.settings = settings
        app.state.conv_state = state

        logger.debug("Initialising Telegram application...")
        await tg_app.initialize()

        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(_run_ptb_polling, tg_app)
                logger.info(
                    "PumpPal ready -- FastAPI on {}:{} + Telegram polling active",
                    settings.host,
                    settings.port,
                )
                yield
                logger.info("Shutdown signal received -- stopping task group")
                tg.cancel_scope.cancel()
        finally:
            logger.info("Shutting down Telegram and Hevy client...")
            await tg_app.shutdown()
            await hevy_client.aclose()
            logger.info("PumpPal shutdown complete")

    fast_app = FastAPI(title="PumpPal", lifespan=lifespan)
    fast_app.include_router(webhook_router)
    logger.debug("FastAPI app created with webhook router")
    return fast_app


async def _run_ptb_polling(tg_app: Any) -> None:
    """Run PTB polling as a coroutine inside the existing event loop."""
    logger.debug("Starting Telegram polling...")
    await tg_app.start()
    updater = tg_app.updater
    assert updater is not None
    await updater.start_polling(allowed_updates=["message"])
    logger.info("Telegram polling active")
    try:
        await asyncio.Event().wait()
    finally:
        logger.debug("Stopping Telegram updater...")
        await updater.stop()
        await tg_app.stop()
        logger.info("Telegram polling stopped")


def main() -> None:
    settings = get_settings()
    setup_logging()
    logger.info(
        "PumpPal launching -- host={} port={} model={}",
        settings.host,
        settings.port,
        settings.openrouter_model,
    )
    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_config=None,  # disable uvicorn's default logging (loguru handles it)
    )


if __name__ == "__main__":
    main()
