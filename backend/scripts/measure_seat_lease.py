#!/usr/bin/env python3
"""一个座位一次准入在 Valkey 上花掉几个来回、每个来回多贵。真服务器上量，不估。

    cd backend && python -m scripts.measure_seat_lease --redis-url redis://127.0.0.1:6379/0

给的是**服务器的地址**，所以谁都能把这个数复现一遍：拿 dev 那台 Valkey 的地址跑，
量到的就是 dev 上的代价。容器/回环上跑出来的数要照实说是本机的数，不是 dev 的数。

计数是拿命令数出来的，不是按代码推的：客户端每次出手都记一条，跑完和这里的预期
对不上就以 1 退出——预期写错、或者中途多了一个来回，都会当场红。

座位一次准入发三条命令：SET NX PX 拿下、紧接着一次续租的 EVAL、放手时再一条 EVAL。
中间那条是 `admission.enter` 里立刻给刚写上的 TTL 续一次期，两个命令可以合成一个
来回（下面量了这么做能到多少）；少发一条属于 `enter`，项目准入也走它，不是座位这一步
能单独改的。
"""

import argparse
import asyncio
import statistics
import sys
import time
import uuid

from redis.asyncio import Redis

from app.core.config import settings
from app.domain.agent import admission
from app.domain.agent.admission import Hold, Lease, enter

#: 一条命令一个来回，不被等的情况下。SET + 续租 + 放手。
TAKE_AND_RELEASE = ["SET", "EVAL", "EVAL"]


class Counting(Redis):
    """把出手的命令记下来的客户端。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.commands: list[str] = []

    async def execute_command(self, *args, **options):
        self.commands.append(str(args[0]).upper())
        return await super().execute_command(*args, **options)


def _percentiles(seconds: list[float]) -> str:
    ordered = sorted(seconds)
    p50 = statistics.median(ordered)
    p95 = ordered[int(len(ordered) * 0.95) - 1]
    return f"p50={p50 * 1000:7.3f}ms  p95={p95 * 1000:7.3f}ms"


async def _timed(rounds: int, warmup: int, one) -> list[float]:
    """跑 ``warmup`` 轮热身，再正式跑 ``rounds`` 轮，返回每轮花掉的秒数。"""
    for _ in range(warmup):
        await one()
    samples: list[float] = []
    for _ in range(rounds):
        started = time.perf_counter()
        await one()
        samples.append(time.perf_counter() - started)
    return samples


async def _measure(redis: Counting, rounds: int, warmup: int) -> int:
    code = 0
    ping = await redis.ping()
    assert ping, "the server did not answer"
    info = await redis.execute_command("INFO", "server")
    if isinstance(info, dict):  # redis-py parses INFO into a mapping for us
        version = info.get("redis_version", "?")
    else:  # a raw client hands back the text
        version = next(
            line.split(":", 1)[1]
            for line in info.decode().splitlines()
            if line.startswith("redis_version")
        )
    print(
        f"server {version} at {redis.connection_pool.connection_kwargs['host']}, "
        f"{rounds} rounds after {warmup} warm-up\n"
    )

    async def one_admission() -> None:
        admission.get_redis_client = lambda: redis
        lease = Lease(key)
        redis.commands.clear()
        async with lease:
            pass
        counts.append(tuple(redis.commands))

    key = f"measure:seat:{uuid.uuid4()}"
    counts: list[tuple[str, ...]] = []
    samples = await _timed(rounds, warmup, one_admission)
    print(
        f"one uncontended admission   {_percentiles(samples)}   "
        f"commands={counts[-1]} ({len(counts[-1])} round trips)"
    )

    async def one_round_trip() -> None:
        redis.commands.clear()
        await redis.ping()

    samples = await _timed(rounds, warmup, one_round_trip)
    print(f"one bare round trip (PING)  {_percentiles(samples)}")

    async def one_take() -> None:
        ticket = str(uuid.uuid4())
        redis.commands.clear()
        slot = await enter(redis, ticket, hold=Hold(key))
        await slot.release()

    samples = await _timed(rounds, warmup, one_take)
    print(
        f"the same through enter()    {_percentiles(samples)}   "
        f"commands={redis.commands}"
    )

    async def pipelined_take() -> None:
        ticket = str(uuid.uuid4())
        redis.commands.clear()
        async with redis.pipeline(transaction=False) as pipe:
            # 现在座位放手时 EVAL 一条、拿走时 SET+EVAL 两条。合起来发是一个来回。
            pipe.set(key, ticket, nx=True, px=admission._ms(admission.LEASE_S))
            pipe.eval(
                admission._RENEW_HOLD, 1, key, ticket, admission._ms(admission.LEASE_S)
            )
            pipe.eval(admission._DROP_HOLD, 1, key, ticket)
            await pipe.execute()

    samples = await _timed(rounds, warmup, pipelined_take)
    print(f"those three in one round trip {_percentiles(samples)}")

    # 等着的那个：等不到就每 POLL_S 一个来回，不是 0 个。
    key = f"measure:waited:{uuid.uuid4()}"
    admission.POLL_S = 1.0
    lease = Lease(key)

    async def hold_for(seconds: float) -> None:
        admission.get_redis_client = lambda: redis
        async with lease:
            await asyncio.sleep(seconds)

    holder = asyncio.create_task(hold_for(3.2))
    await asyncio.sleep(0.2)
    redis.commands.clear()
    started = time.perf_counter()
    async with lease:
        pass
    waited = time.perf_counter() - started
    asks = sum(1 for command in redis.commands if command == "SET")
    print(
        f"\na waiter behind a 3.2s holder: waited {waited:.2f}s, {asks} asks for the "
        f"seat, one per POLL_S={admission.POLL_S}s, {len(redis.commands)} commands "
        f"in all: {tuple(redis.commands)}. An asyncio.Lock is taken with no "
        f"round trip; this is not"
    )
    await holder

    if any(len(count) != len(TAKE_AND_RELEASE) for count in counts):
        offenders = {count for count in counts if len(count) != len(TAKE_AND_RELEASE)}
        print(
            f"\nFAIL: an admission sent {offenders}, expected {TAKE_AND_RELEASE}",
            file=sys.stderr,
        )
        code = 1
    elif counts[-1] != tuple(TAKE_AND_RELEASE):
        print(
            f"\nFAIL: an admission sent {counts[-1]}, expected {TAKE_AND_RELEASE}",
            file=sys.stderr,
        )
        code = 1
    return code


async def main(url: str, rounds: int, warmup: int) -> int:
    redis = Counting.from_url(url)
    try:
        return await _measure(redis, rounds, warmup)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--redis-url",
        default=settings.redis_url,
        help="the Valkey/Redis to measure against (default: the app's own)",
    )
    parser.add_argument("--rounds", type=int, default=300)
    parser.add_argument("--warmup", type=int, default=30)
    arguments = parser.parse_args()
    sys.exit(asyncio.run(main(arguments.redis_url, arguments.rounds, arguments.warmup)))
