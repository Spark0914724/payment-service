import asyncio
import json
import logging
import random
import uuid
from datetime import datetime, timezone

import httpx
from aio_pika import IncomingMessage, connect_robust
from sqlalchemy import select

from app.core.config import settings
from app.core.broker import setup_rabbitmq
from app.db.session import AsyncSessionLocal
from app.models.payment import Payment, PaymentStatus

logger = logging.getLogger(__name__)


async def send_webhook(url: str, payload: dict, retries: int = 3) -> None:
    delay = 1
    async with httpx.AsyncClient(timeout=10) as client:
        for attempt in range(1, retries + 1):
            try:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                logger.info("Webhook delivered to %s on attempt %d", url, attempt)
                return
            except Exception as e:
                logger.warning("Webhook attempt %d failed: %s", attempt, e)
                if attempt < retries:
                    await asyncio.sleep(delay)
                    delay *= 2  # exponential backoff
    logger.error("Webhook delivery failed after %d attempts to %s", retries, url)


async def process_message(message: IncomingMessage) -> None:
    async with message.process(requeue=False):
        try:
            payload = json.loads(message.body)
            payment_id = uuid.UUID(payload["payment_id"])
        except Exception as e:
            logger.error("Invalid message body: %s", e)
            return

        # Emulate processing delay 2-5 seconds
        await asyncio.sleep(random.uniform(2, 5))

        # 90% success, 10% failure
        success = random.random() < 0.9
        new_status = PaymentStatus.SUCCEEDED if success else PaymentStatus.FAILED

        async with AsyncSessionLocal() as db:
            payment = await db.scalar(select(Payment).where(Payment.id == payment_id))
            if not payment:
                logger.error("Payment %s not found", payment_id)
                return

            payment.status = new_status
            payment.processed_at = datetime.now(timezone.utc)
            await db.commit()
            logger.info("Payment %s -> %s", payment_id, new_status)

            if payment.webhook_url:
                await send_webhook(
                    payment.webhook_url,
                    {
                        "payment_id": str(payment.id),
                        "status": new_status,
                        "processed_at": payment.processed_at.isoformat(),
                    },
                )


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("Consumer starting...")

    await setup_rabbitmq()

    connection = await connect_robust(settings.RABBITMQ_URL)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=10)
        queue = await channel.get_queue("payments.new")
        await queue.consume(process_message)
        logger.info("Consumer listening on payments.new")
        await asyncio.Future()  # run forever
