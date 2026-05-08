import asyncio
from contextlib import asynccontextmanager

import structlog
import structlog.stdlib
from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)

from routes import poll_sessions, router


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(poll_sessions())
    yield
    task.cancel()


app = FastAPI(title="devin-autoremediation-webhook", lifespan=lifespan)
app.include_router(router)
