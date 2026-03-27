import asyncio
import json
import logging
from datetime import datetime, timezone

from aio_pika import DeliveryMode, Message
from sqlalchemy import select

from app.core.broker import get_connection
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.outbox import OutboxMessage

logger = logging.getLogger(__name__)


async def publish_pending_messages() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(OutboxMessage)
            .where(OutboxMessage.published == False)  # noqa: E712
            .order_by(OutboxMessage.created_at)
            .limit(100)
        )
        messages = result.scalars().all()

        if not messages:
            return

        connection = await get_connection()
        channel = await connection.channel()
        exchange = await channel.get_exchange("payments")

        for msg in messages:
            try:
                await exchange.publish(
                    Message(
                        body=json.dumps(msg.payload).encode(),
                        delivery_mode=DeliveryMode.PERSISTENT,
                        message_id=str(msg.id),
                    ),
                    routing_key="payments.new",
                )
                msg.published = True
                msg.published_at = datetime.now(timezone.utc)
            except Exception as e:
                logger.error("Failed to publish outbox message %s: %s", msg.id, e)

        await db.commit()
        await channel.close()


async def run_outbox_scheduler() -> None:
    while True:
        try:
            await publish_pending_messages()
        except Exception as e:
            logger.error("Outbox error: %s", e)
        await asyncio.sleep(settings.OUTBOX_INTERVAL)
