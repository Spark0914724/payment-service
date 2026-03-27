from fastapi import FastAPI

app = FastAPI(title="Payment Service", version="1.0.0")


@app.get("/health")
async def health():
    return {"status": "ok"}
