import asyncio
import json
import logging
import random
import uuid
from datetime import datetime, timezone

import httpx
from aio_pika import IncomingMessage, Message, connect_robust
from aio_pika.abc import AbstractRobustConnection
from sqlalchemy import select

from app.core.broker import setup_rabbitmq
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.payment import Payment, PaymentStatus

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


async def send_webhook(url: str, payload: dict) -> None:
    """Send webhook with exponential backoff, 3 attempts."""
    delay = 1
    async with httpx.AsyncClient(timeout=10) as client:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                logger.info("Webhook delivered to %s on attempt %d", url, attempt)
                return
            except Exception as e:
                logger.warning("Webhook attempt %d/%d failed: %s", attempt, MAX_RETRIES, e)
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(delay)
                    delay *= 2
    logger.error("Webhook delivery exhausted after %d attempts to %s", MAX_RETRIES, url)


def get_retry_count(message: IncomingMessage) -> int:
    """Read x-retry-count header from message, default 0."""
    headers = message.headers or {}
    return int(headers.get("x-retry-count", 0))


async def requeue_with_retry(
    connection: AbstractRobustConnection,
    message: IncomingMessage,
    retry_count: int,
) -> None:
    """Re-publish message with incremented retry count and exponential delay."""
    delay = (2 ** retry_count)  # 1s, 2s, 4s
    logger.warning("Requeueing message, attempt %d/%d, delay %ds", retry_count + 1, MAX_RETRIES, delay)
    await asyncio.sleep(delay)

    channel = await connection.channel()
    exchange = await channel.get_exchange("payments")
    await exchange.publish(
        Message(
            body=message.body,
            delivery_mode=message.delivery_mode,
            message_id=message.message_id,
            headers={"x-retry-count": retry_count + 1},
        ),
        routing_key="payments.new",
    )
    await channel.close()


async def process_message(message: IncomingMessage, connection: AbstractRobustConnection) -> None:
    retry_count = get_retry_count(message)

    try:
        payload = json.loads(message.body)
        payment_id = uuid.UUID(payload["payment_id"])
    except Exception as e:
        logger.error("Invalid message body, sending to DLQ: %s", e)
        await message.reject(requeue=False)
        return

    try:
        # Emulate processing: 2-5s delay, 90% success / 10% failure
        await asyncio.sleep(random.uniform(2, 5))
        success = random.random() < 0.9
        new_status = PaymentStatus.SUCCEEDED.value if success else PaymentStatus.FAILED.value

        async with AsyncSessionLocal() as db:
            payment = await db.scalar(select(Payment).where(Payment.id == payment_id))
            if not payment:
                logger.error("Payment %s not found", payment_id)
                await message.ack()
                return

            payment.status = new_status
            payment.processed_at = datetime.now(timezone.utc)
            await db.commit()
            logger.info("Payment %s -> %s", payment_id, new_status)

        if payload.get("webhook_url"):
            await send_webhook(
                payload["webhook_url"],
                {
                    "payment_id": str(payment_id),
                    "status": new_status,  # already a string value
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                },
            )

        await message.ack()

    except Exception as e:
        logger.error("Error processing payment %s: %s", payment_id, e)
        if retry_count < MAX_RETRIES - 1:
            await message.ack()  # ack original, re-publish with retry count
            await requeue_with_retry(connection, message, retry_count)
        else:
            logger.error("Payment %s exhausted retries, sending to DLQ", payment_id)
            await message.reject(requeue=False)  # RabbitMQ routes to DLQ


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("Consumer starting...")

    await setup_rabbitmq()

    connection = await connect_robust(settings.RABBITMQ_URL)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=10)
        queue = await channel.get_queue("payments.new")

        async def on_message(message: IncomingMessage) -> None:
            await process_message(message, connection)

        await queue.consume(on_message)
        logger.info("Consumer listening on payments.new")
        await asyncio.Future()  # run forever
