import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1 import router as api_router
from app.core.broker import close_connection, setup_rabbitmq
from app.core.outbox import run_outbox_scheduler

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await setup_rabbitmq()
    task = asyncio.create_task(run_outbox_scheduler())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await close_connection()


app = FastAPI(title="Payment Service", version="1.0.0", lifespan=lifespan)

app.include_router(api_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
