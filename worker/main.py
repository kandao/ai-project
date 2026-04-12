import asyncio
import json
import logging
import signal

from aiokafka import AIOKafkaConsumer

from config import settings
import pipeline
from storage import create_pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


async def run() -> None:
    pool = await create_pool(settings.DATABASE_URL)

    consumer = AIOKafkaConsumer(
        settings.KAFKA_TOPIC_INGEST,
        settings.KAFKA_TOPIC_DELETE,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id=settings.KAFKA_GROUP_ID,
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    await consumer.start()

    running = True
    loop = asyncio.get_running_loop()

    def handle_sigterm(*_):
        nonlocal running
        running = False
        logging.info("SIGTERM received, shutting down...")
        loop.call_soon_threadsafe(loop.stop)

    signal.signal(signal.SIGTERM, handle_sigterm)

    logging.info(
        f"Worker listening on {settings.KAFKA_TOPIC_INGEST}, {settings.KAFKA_TOPIC_DELETE}"
    )

    try:
        async for msg in consumer:
            if not running:
                break
            try:
                if msg.topic == settings.KAFKA_TOPIC_INGEST:
                    await pipeline.process(msg.value, pool)
                elif msg.topic == settings.KAFKA_TOPIC_DELETE:
                    await pipeline.delete(msg.value, pool)
                await consumer.commit()
            except Exception as e:
                logging.error(
                    f"Error processing message from {msg.topic}: {e}", exc_info=True
                )
                await consumer.commit()  # don't reprocess poison messages
    finally:
        await consumer.stop()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
