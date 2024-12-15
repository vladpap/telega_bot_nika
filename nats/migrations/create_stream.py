import asyncio
import os
import sys

import nats
from nats.js.api import StreamConfig  # type: ignore
from config.config import settings

sys.path.insert(
    0, os.path.abspath(
        os.path.join(
            os.path.dirname(__file__), "../../")))


async def main():
    nc = await nats.connect(servers=settings.nats.servers)

    js = nc.jetstream()

    stream_name = settings.nats.delayed_consumer_stream

    # Конфигурация стрима
    config = StreamConfig(
        name=stream_name,
        subjects=[settings.nats.delayed_consumer_subject],
        retention="limits",  # Политика хранения сообщений
                             # (limits, interest, workqueue)
        storage="file"  # Тип хранения сообщений (file, memory)
    )

    # Создание стрима
    await js.add_stream(config)

    print(f"Stream `{stream_name}` created")

    # Закрытие соединения
    await nc.close()


asyncio.run(main())
