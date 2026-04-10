import json
import logging

from aiokafka import AIOKafkaProducer

from config import settings

logger = logging.getLogger(__name__)


class KafkaProducerService:
    """
    Singleton wrapper around AIOKafkaProducer.

    Usage:
        await kafka_producer.start()   # on app startup
        await kafka_producer.send("doc.ingest", {...})
        await kafka_producer.stop()    # on app shutdown
    """

    def __init__(self, bootstrap_servers: str):
        self._bootstrap_servers = bootstrap_servers
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        """Initialize and start the Kafka producer."""
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            # Ensure all replicas acknowledge the write
            acks="all",
            # Retry on transient failures
            retry_backoff_ms=200,
        )
        await self._producer.start()
        logger.info("Kafka producer started (servers=%s)", self._bootstrap_servers)

    async def stop(self) -> None:
        """Flush pending messages and stop the producer."""
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None
            logger.info("Kafka producer stopped")

    async def send(self, topic: str, payload: dict) -> None:
        """
        Serialize *payload* to JSON and publish to *topic*.

        Topics used by this service:
          - doc.ingest  — triggers the ingestion worker
          - doc.delete  — triggers vector cleanup in the worker
          - chat.query  — consumed by the agent
        """
        if self._producer is None:
            raise RuntimeError("KafkaProducerService is not started. Call start() first.")

        await self._producer.send_and_wait(topic, payload)
        logger.debug("Published to Kafka topic=%s payload=%s", topic, payload)


# Module-level singleton
kafka_producer = KafkaProducerService(
    bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
)
