# Payment Service

Async payment processing microservice built with FastAPI, RabbitMQ, and PostgreSQL.

## Stack

- FastAPI + Pydantic v2
- SQLAlchemy 2.0 (async)
- PostgreSQL 16
- RabbitMQ 3.13
- Alembic migrations
- Docker + docker-compose

## Architecture

```
Client
  │
  ▼
POST /api/v1/payments
  │  writes payment + outbox row (same transaction)
  ▼
Outbox Scheduler (every 5s)
  │  publishes unpublished outbox rows
  ▼
RabbitMQ: payments.new
  │
  ▼
Consumer
  │  emulates processing (2-5s, 90% success / 10% fail)
  │  updates payment status in DB
  │  sends webhook (3 retries, exponential backoff)
  │
  └─ on failure after 3 retries ──► payments.dlq
```

## Quick Start

**1. Clone and configure:**
```bash
cp .env.example .env
```

**2. Start everything:**
```bash
docker-compose up --build
```

This starts: postgres, rabbitmq, runs migrations, then starts api and consumer.

**3. Run migrations separately (optional):**
```bash
docker-compose up migrate
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://payment:payment@postgres:5432/payment_db` | Async DB URL |
| `RABBITMQ_URL` | `amqp://guest:guest@rabbitmq:5672/` | RabbitMQ connection |
| `API_KEY` | `supersecretapikey` | Static API key for X-API-Key header |
| `OUTBOX_INTERVAL` | `5` | Outbox polling interval in seconds |

## API

All endpoints require the header: `X-API-Key: <your_api_key>`

### Create Payment

```
POST /api/v1/payments
Headers:
  X-API-Key: supersecretapikey
  Idempotency-Key: <unique-key>
  Content-Type: application/json

Body:
{
  "amount": 100.00,
  "currency": "RUB",
  "description": "Order #123",
  "metadata": {"order_id": "123"},
  "webhook_url": "https://example.com/webhook"
}

Response 202:
{
  "payment_id": "uuid",
  "status": "pending",
  "created_at": "2026-03-28T00:00:00Z"
}
```

### Get Payment

```
GET /api/v1/payments/{payment_id}
Headers:
  X-API-Key: supersecretapikey

Response 200:
{
  "id": "uuid",
  "amount": "100.00",
  "currency": "RUB",
  "description": "Order #123",
  "metadata": {"order_id": "123"},
  "status": "succeeded",
  "idempotency_key": "unique-key",
  "webhook_url": "https://example.com/webhook",
  "created_at": "2026-03-28T00:00:00Z",
  "processed_at": "2026-03-28T00:00:05Z"
}
```

## Example curl Commands

**Create payment:**
```bash
curl -X POST http://localhost:8000/api/v1/payments \
  -H "Content-Type: application/json" \
  -H "X-API-Key: supersecretapikey" \
  -H "Idempotency-Key: order-001" \
  -d '{"amount": 100.00, "currency": "RUB", "description": "Test"}'
```

**Get payment:**
```bash
curl http://localhost:8000/api/v1/payments/<payment_id> \
  -H "X-API-Key: supersecretapikey"
```

**PowerShell:**
```powershell
Invoke-WebRequest -Uri "http://localhost:8000/api/v1/payments" `
  -Method POST `
  -Headers @{"X-API-Key"="supersecretapikey"; "Idempotency-Key"="order-001"; "Content-Type"="application/json"} `
  -Body '{"amount": 100.00, "currency": "RUB", "description": "Test"}'
```

## Swagger UI

Open `http://localhost:8000/docs` to explore and test the API interactively.

## RabbitMQ Management

Open `http://localhost:15672` — login: `guest / guest`

Queues:
- `payments.new` — main processing queue
- `payments.dlq` — dead letter queue (messages failed after 3 retries)

## Key Design Decisions

**Outbox Pattern** — payment and outbox row are written in a single DB transaction, guaranteeing the event is never lost even if RabbitMQ is temporarily unavailable.

**Idempotency** — duplicate requests with the same `Idempotency-Key` return the existing payment without creating a new one.

**Retry Logic** — consumer retries failed messages 3 times with exponential backoff (1s, 2s, 4s). After exhausting retries, the message is routed to `payments.dlq`.

**Webhook Retries** — webhook delivery is retried independently up to 3 times with exponential backoff (1s, 2s, 4s).
