# Payment Service

Async payment processing microservice built with FastAPI, RabbitMQ, and PostgreSQL.

## Stack

- FastAPI + Pydantic v2
- SQLAlchemy 2.0 (async)
- PostgreSQL 16
- RabbitMQ 3.13
- Alembic
- Docker + docker-compose

## How it works

When a payment is created, the API writes both the payment and an outbox event in a single transaction. A background scheduler picks up unpublished outbox rows and pushes them to RabbitMQ. The consumer processes each message, updates the payment status, and fires a webhook to the provided URL.

If processing fails, the message is retried up to 3 times with exponential backoff (1s → 2s → 4s). After that it lands in `payments.dlq`. Webhook delivery has the same retry logic independently.

```
POST /api/v1/payments
  └─ writes payment + outbox row (same tx)

Outbox scheduler (every 5s)
  └─ publishes to payments.new

Consumer
  └─ processes, updates DB, sends webhook
  └─ on failure x3 → payments.dlq
```

## Setup

```bash
cp .env.example .env
docker-compose up --build
```

That's it. Migrations run automatically before the API starts.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://payment:payment@postgres:5432/payment_db` | DB connection |
| `RABBITMQ_URL` | `amqp://guest:guest@rabbitmq:5672/` | RabbitMQ connection |
| `API_KEY` | `supersecretapikey` | Value for `X-API-Key` header |
| `OUTBOX_INTERVAL` | `5` | Outbox polling interval (seconds) |

## API

All requests need `X-API-Key: <your_api_key>` header.

### POST /api/v1/payments

Also requires `Idempotency-Key` header. Sending the same key twice returns the original payment without creating a duplicate.

```json
{
  "amount": 100.00,
  "currency": "RUB",
  "description": "Order #123",
  "metadata": {"order_id": "123"},
  "webhook_url": "https://example.com/webhook"
}
```

Response `202`:
```json
{
  "payment_id": "uuid",
  "status": "pending",
  "created_at": "2026-03-28T00:00:00Z"
}
```

### GET /api/v1/payments/{payment_id}

Returns full payment details including final status once processed.

## Examples

```bash
curl -X POST http://localhost:8000/api/v1/payments \
  -H "Content-Type: application/json" \
  -H "X-API-Key: supersecretapikey" \
  -H "Idempotency-Key: order-001" \
  -d '{"amount": 100.00, "currency": "RUB", "description": "Test"}'

curl http://localhost:8000/api/v1/payments/<payment_id> \
  -H "X-API-Key: supersecretapikey"
```

PowerShell:
```powershell
Invoke-WebRequest -Uri "http://localhost:8000/api/v1/payments" `
  -Method POST `
  -Headers @{"X-API-Key"="supersecretapikey"; "Idempotency-Key"="order-001"; "Content-Type"="application/json"} `
  -Body '{"amount": 100.00, "currency": "RUB", "description": "Test"}'
```

Swagger UI: `http://localhost:8000/docs`

RabbitMQ management: `http://localhost:15672` (guest / guest)
