"""API Gateway HTTP API entry point; local development still uses Uvicorn."""

import asyncio

from mangum import Mangum

from app.main import app

adapter = Mangum(app, lifespan="off")


def handler(event, context):
    # Python 3.14 no longer creates a main-thread loop implicitly. Mangum 0.19
    # drives ASGI with get_event_loop(), so provide a reusable loop when needed.
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    if loop.is_closed():
        asyncio.set_event_loop(asyncio.new_event_loop())
    return adapter(event, context)
