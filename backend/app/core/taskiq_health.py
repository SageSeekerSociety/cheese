import argparse
import asyncio
import json
import math
import os
import time

from redis.asyncio import Redis

from app.core.config import settings


def make_heartbeat(*, now: float | None = None, release: str | None = None) -> str:
    """Return the worker effect recorded by the scheduled heartbeat task."""
    timestamp = time.time() if now is None else now
    current_release = release or os.getenv("CHEESE_TASKIQ_RELEASE", "unknown")
    return json.dumps(
        {"timestamp": timestamp, "release": current_release},
        separators=(",", ":"),
    )


def heartbeat_is_fresh(
    raw: bytes | str | None,
    *,
    now: float | None = None,
    max_age_seconds: int,
    expected_release: str,
) -> bool:
    """Accept only a recent worker effect produced by the expected release."""
    if raw is None:
        return False
    if isinstance(raw, bytes):
        raw = raw.decode(errors="replace")
    try:
        data = json.loads(raw)
        timestamp = float(data["timestamp"])
        release = str(data["release"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    if not math.isfinite(timestamp) or release != expected_release:
        return False
    age = (time.time() if now is None else now) - timestamp
    return 0 <= age <= max_age_seconds


async def publish_heartbeat() -> dict[str, str]:
    """Write a short-lived proof that scheduler, broker, and worker all ran."""
    payload = make_heartbeat()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis.set(
            settings.taskiq_heartbeat_key,
            payload,
            ex=settings.taskiq_heartbeat_max_age_seconds * 2,
        )
    finally:
        await redis.aclose()
    return {"heartbeat": payload}


async def check_heartbeat(*, max_age_seconds: int | None = None) -> bool:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        raw = await redis.get(settings.taskiq_heartbeat_key)
    finally:
        await redis.aclose()
    return heartbeat_is_fresh(
        raw,
        max_age_seconds=(
            settings.taskiq_heartbeat_max_age_seconds
            if max_age_seconds is None
            else max_age_seconds
        ),
        expected_release=os.getenv("CHEESE_TASKIQ_RELEASE", "unknown"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the Taskiq effect heartbeat")
    parser.add_argument("--max-age", type=int, default=None)
    args = parser.parse_args()
    return 0 if asyncio.run(check_heartbeat(max_age_seconds=args.max_age)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
