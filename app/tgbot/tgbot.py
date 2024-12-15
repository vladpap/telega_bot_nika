import asyncio
import logging

import psycopg_pool  # type: ignore
import redis  # type: ignore
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import ExceptionTypeFilter
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram_dialog import setup_dialogs  # type: ignore
from aiogram_dialog.api.exceptions import \
    UnknownIntent, UnknownState
from fluentogram import TranslatorHub  # type: ignore

from app.infrastructure.cache.utils.connect_to_redis import get_redis_pool
from app.infrastructure.database.utils.connect_to_pg import get_pg_pool
from app.infrastructure.storage.storage.nats_storage import NatsStorage
from app.infrastructure.storage.utils.nats_connect import connect_to_nats
from app.services.delay_service.utils.start_consumer import \
    start_delayed_consumer
from app.tgbot.dialogs.start.dialogs import start_dialog
from app.tgbot.handlers.commands import commands_router
from app.tgbot.handlers.errors import on_unknown_intent, on_unknown_state
from app.tgbot.middlewares.database import DataBaseMiddleware
from app.tgbot.middlewares.i18n import TranslatorRunnerMiddleware
from app.tgbot.middlewares.setlang import SetLangMiddleware
from app.tgbot.utils.i18n import create_translator_hub
from config.config import settings

logger = logging.getLogger(__name__)


async def main():
    logger.info("Starting bot")

    nc, js = await connect_to_nats(servers=settings.nats.servers)

    storage: NatsStorage = await NatsStorage(
        nc=nc,
        js=js,
        key_builder=DefaultKeyBuilder(with_destiny=True)
    ).create_storage()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode(settings.bot.parse_mode))
    )
    dp = Dispatcher(storage=storage)

    if settings.cache.use_cache:
        cache_pool: redis.asyncio.Redis = await get_redis_pool(
            db=settings.redis.database,
            host=settings.redis.host,
            port=settings.redis.port,
            username=settings.redis_username,
            password=settings.redis_password,
        )
        dp.workflow_data.update(_cache_pool=cache_pool)

    db_pool: psycopg_pool.AsyncConnectionPool = await get_pg_pool(
        db_name=settings.postgres.name,
        host=settings.postgres.host,
        port=settings.postgres.port,
        user=settings.postgres_user,
        password=settings.postgres_password,
    )

    translator_hub: TranslatorHub = create_translator_hub()

    logger.info("Registering error handlers")
    dp.errors.register(
        on_unknown_intent,
        ExceptionTypeFilter(UnknownIntent),
    )
    dp.errors.register(
        on_unknown_state,
        ExceptionTypeFilter(UnknownState),
    )

    logger.info("Including routers")
    dp.include_routers(commands_router, start_dialog)

    logger.info("Including middlewares")
    dp.update.middleware(DataBaseMiddleware())
    dp.update.middleware(SetLangMiddleware())
    dp.update.middleware(TranslatorRunnerMiddleware())
    dp.errors.middleware(DataBaseMiddleware())
    dp.errors.middleware(TranslatorRunnerMiddleware())
    dp.errors.middleware(SetLangMiddleware())

    bg_factory = setup_dialogs(dp)

    # Launch polling and delayed message consumer
    try:
        await asyncio.gather(
            dp.start_polling(
                bot,
                js=js,
                delay_del_subject=settings.nats.delayed_consumer_subject,
                bg_factory=bg_factory,
                _translator_hub=translator_hub,
                _db_pool=db_pool
            ),
            start_delayed_consumer(
                nc=nc,
                js=js,
                bot=bot,
                subject=settings.nats.delayed_consumer_subject,
                stream=settings.nats.delayed_consumer_stream,
                durable_name=settings.nats.delayed_consumer_durable_name
            )
        )
    except Exception as e:
        logger.exception(e)
    finally:
        await nc.close()
        logger.info('Connection to NATS closed')
        await db_pool.close()
        logger.info('Connection to Postgres closed')
        if dp.workflow_data.get('_cache_pool'):
            await cache_pool.close()
            logger.info('Connection to Redis closed')
