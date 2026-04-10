import logging
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from database import create_tables
from middleware.rate_limit import init_rate_limiter
from routers import analytics, chat, documents, internal
from services.kafka_producer import kafka_producer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# Module-level Redis client shared across request handlers
redis_client: aioredis.Redis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.

    Startup:
      - Start Kafka producer
      - Connect to Redis and initialise the rate limiter
      - Create all SQLAlchemy-managed database tables

    Shutdown:
      - Stop Kafka producer
      - Close Redis connection
    """
    global redis_client

    # ── Startup ──────────────────────────────────────────────────────────
    logger.info("Starting backend services…")

    # Kafka
    await kafka_producer.start()
    logger.info("Kafka producer ready")

    # Redis
    redis_client = aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=False,  # We decode manually where needed
    )
    await redis_client.ping()
    logger.info("Redis connection established")

    # Inject Redis into the chat router's SSE relay
    chat.set_redis_client(redis_client)

    # Initialise the sliding-window rate limiter
    init_rate_limiter(redis_client)
    logger.info("Rate limiter initialised (max=%d req/min)", settings.RATE_LIMIT_REQUESTS_PER_MINUTE)

    # Database tables
    await create_tables()
    logger.info("Database tables verified/created")

    logger.info("Backend startup complete — listening on port 8000")

    yield  # ── Application runs here ──

    # ── Shutdown ─────────────────────────────────────────────────────────
    logger.info("Shutting down backend services…")

    await kafka_producer.stop()
    logger.info("Kafka producer stopped")

    if redis_client:
        await redis_client.close()
        logger.info("Redis connection closed")


app = FastAPI(
    title="DocQA Backend",
    description=(
        "FastAPI gateway for the DocQA system. Handles document uploads, "
        "chat queries, SSE relay, and per-user DB credential management."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
# Public routes — JWT required at the route function level via Depends(get_current_user)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(analytics.router)

# Internal route — NO JWT dependency; protected by Docker network isolation only
app.include_router(internal.router)


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    """Liveness probe — returns 200 when the service is running."""
    return {"status": "ok"}
