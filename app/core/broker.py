import logging

from aio_pika import connect_robust
from aio_pika.abc import AbstractRobustConnection

from app.core.config import settings

logger = logging.getLogger(__name__)

_connection: AbstractRobustConnection | None = None


async def get_connection() -> AbstractRobustConnection:
    global _connection
    if _connection is None or _connection.is_closed:
        _connection = await connect_robust(settings.RABBITMQ_URL)
    return _connection


async def setup_rabbitmq() -> None:
    """Declare all exchanges, queues and bindings."""
    connection = await get_connection()
    async with connection.channel() as channel:
        # Dead letter exchange
        dlx = await channel.declare_exchange("payments.dlx", type="direct", durable=True)

        # Dead letter queue
        dlq = await channel.declare_queue("payments.dlq", durable=True)
        await dlq.bind(dlx, routing_key="payments.new")

        # Main exchange
        exchange = await channel.declare_exchange("payments", type="direct", durable=True)

        # Main queue with DLX configured
        queue = await channel.declare_queue(
            "payments.new",
            durable=True,
            arguments={
                "x-dead-letter-exchange": "payments.dlx",
                "x-dead-letter-routing-key": "payments.new",
                "x-message-ttl": 60000,  # 60s per attempt before DLQ
            },
        )
        await queue.bind(exchange, routing_key="payments.new")

    logger.info("RabbitMQ topology ready")


async def close_connection() -> None:
    global _connection
    if _connection and not _connection.is_closed:
        await _connection.close()
        _connection = None
