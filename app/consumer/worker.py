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
    delay = 1
    async with httpx.AsyncClient(timeout=10) as client:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                return
            except Exception as e:
                logger.warning("Webhook attempt %d/%d failed: %s", attempt, MAX_RETRIES, e)
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(delay)
                    delay *= 2
    logger.error("Webhook delivery failed after %d attempts to %s", MAX_RETRIES, url)


def get_retry_count(message: IncomingMessage) -> int:
    headers = message.headers or {}
    return int(headers.get("x-retry-count", 0))


async def requeue_with_retry(
    connection: AbstractRobustConnection,
    message: IncomingMessage,
    retry_count: int,
) -> None:
    delay = 2 ** retry_count
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
        logger.error("Invalid message body: %s", e)
        await message.reject(requeue=False)
        return

    try:
        await asyncio.sleep(random.uniform(2, 5))
        new_status = PaymentStatus.SUCCEEDED.value if random.random() < 0.9 else PaymentStatus.FAILED.value

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
                    "status": new_status,
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                },
            )

        await message.ack()

    except Exception as e:
        logger.error("Error processing payment %s: %s", payment_id, e)
        if retry_count < MAX_RETRIES - 1:
            await message.ack()
            await requeue_with_retry(connection, message, retry_count)
        else:
            logger.error("Payment %s exhausted retries, moving to DLQ", payment_id)
            await message.reject(requeue=False)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

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
        await asyncio.Future()
