import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import verify_api_key
from app.db.session import get_db
from app.models.payment import Payment, PaymentStatus
from app.models.outbox import OutboxMessage
from app.schemas.payment import PaymentCreate, PaymentCreateResponse, PaymentResponse

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=PaymentCreateResponse)
async def create_payment(
    body: PaymentCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    # Idempotency check
    existing = await db.scalar(
        select(Payment).where(Payment.idempotency_key == idempotency_key)
    )
    if existing:
        return PaymentCreateResponse(
            payment_id=existing.id,
            status=existing.status,
            created_at=existing.created_at,
        )

    payment = Payment(
        id=uuid.uuid4(),
        amount=body.amount,
        currency=body.currency.value,
        description=body.description,
        metadata_=body.metadata,
        status=PaymentStatus.PENDING,
        idempotency_key=idempotency_key,
        webhook_url=str(body.webhook_url) if body.webhook_url else None,
    )
    db.add(payment)

    outbox = OutboxMessage(
        id=uuid.uuid4(),
        event_type="payments.new",
        payload={
            "payment_id": str(payment.id),
            "amount": str(payment.amount),
            "currency": payment.currency,
            "description": payment.description,
            "webhook_url": payment.webhook_url,
        },
        published=False,
    )
    db.add(outbox)

    await db.commit()

    return PaymentCreateResponse(
        payment_id=payment.id,
        status=payment.status,
        created_at=payment.created_at,
    )


@router.get("/{payment_id}", response_model=PaymentResponse)
async def get_payment(
    payment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    payment = await db.scalar(select(Payment).where(Payment.id == payment_id))
    if not payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    return payment
