import time

from fastapi import Depends, HTTPException, status
import redis.asyncio as aioredis

from config import settings


class RateLimiter:
    """
    Sliding window rate limiter backed by Redis sorted sets.

    Each request is stored as a member of a sorted set keyed by user_id.
    Score = timestamp. Old entries outside the window are pruned before each check.
    """

    def __init__(self, redis_client: aioredis.Redis, max_requests: int, window_seconds: int):
        self.redis = redis_client
        self.max_requests = max_requests
        self.window = window_seconds

    async def check(self, user_id: str) -> None:
        """
        Enforce rate limit for the given user_id.
        Raises HTTP 429 if the limit is exceeded.
        """
        key = f"ratelimit:{user_id}"
        now = time.time()
        window_start = now - self.window

        pipe = self.redis.pipeline()
        # Remove entries older than the sliding window
        pipe.zremrangebyscore(key, 0, window_start)
        # Add the current request with its timestamp as both member and score
        pipe.zadd(key, {str(now): now})
        # Count remaining entries in the window
        pipe.zcard(key)
        # Keep the key alive for at least one window duration
        pipe.expire(key, self.window)
        results = await pipe.execute()

        count = results[2]
        if count > self.max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Rate limit exceeded. Maximum {self.max_requests} requests "
                    f"per {self.window} seconds."
                ),
                headers={"Retry-After": str(self.window)},
            )


# Module-level singleton — populated during app startup
_rate_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    if _rate_limiter is None:
        raise RuntimeError("RateLimiter not initialized. Call init_rate_limiter() on startup.")
    return _rate_limiter


def init_rate_limiter(redis_client: aioredis.Redis) -> RateLimiter:
    """Create and store the singleton RateLimiter. Call once on app startup."""
    global _rate_limiter
    _rate_limiter = RateLimiter(
        redis_client=redis_client,
        max_requests=settings.RATE_LIMIT_REQUESTS_PER_MINUTE,
        window_seconds=60,
    )
    return _rate_limiter


async def rate_limit_dependency(
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
) -> RateLimiter:
    """
    FastAPI dependency placeholder.
    Routes that need per-user rate limiting should call check() explicitly after
    resolving the current user, because we need the user_id at that point.
    This dependency simply returns the limiter instance for injection.
    """
    return rate_limiter
