"""Tell the platform's alert channel when the upstream relay is running out of balance.

The relay's balance is not any team's credits: it is what the platform itself
pays the upstream with, and when it runs dry every project stops at once.
Refusals already reach the alert webhook (a turn that fails with the upstream's
"insufficient balance" says so), but that only happens once it is already too
late to top up before people notice. DeepSeek is the one upstream that publishes
its balance, so a clock reads it and warns while there is still room.

The threshold is a floor in the balance's own currency, not a share of
anything: the balance is topped up by hand, and how many days a given amount
lasts is not something this side of the relay knows. `alerting.send` keeps the
message from repeating more than daily.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from app.core import alerting
from app.core.config import settings

logger = logging.getLogger(__name__)

BALANCE_URL = "https://api.deepseek.com/user/balance"
ALERT_KEY = "ai-relay-balance"


@dataclass(frozen=True)
class Balance:
    """What the upstream says about its own balance, in its own currency."""

    available: bool
    total: float
    granted: float
    topped_up: float
    currency: str


def read(body: dict) -> Balance:
    """Parse the balance endpoint. The one currency DeepSeek reports is CNY;
    should it ever report several, the largest total is the one that matters."""
    infos = body.get("balance_infos") or []
    info = max(infos, key=lambda row: float(row.get("total_balance") or 0), default={})
    return Balance(
        available=bool(body.get("is_available")),
        total=float(info.get("total_balance") or 0),
        granted=float(info.get("granted_balance") or 0),
        topped_up=float(info.get("topped_up_balance") or 0),
        currency=str(info.get("currency") or "CNY"),
    )


async def fetch(api_key: str, *, client: httpx.AsyncClient | None = None) -> Balance:
    headers = {"Authorization": f"Bearer {api_key}"}
    if client is not None:
        answer = await client.get(BALANCE_URL, headers=headers)
        answer.raise_for_status()
        return read(answer.json())
    async with httpx.AsyncClient(timeout=15) as own:
        answer = await own.get(BALANCE_URL, headers=headers)
        answer.raise_for_status()
        return read(answer.json())


def sentence(balance: Balance, floor: float) -> str:
    return f"还剩 {balance.total:g} {balance.currency}，低于 {floor:g} 的报警线"


async def sweep(
    *, fetch_balance: Callable[[str], Awaitable[Balance]] = fetch
) -> Balance | None:
    """The clock's tick. A deployment that holds no upstream key has no balance
    to read, and that is not an error."""
    api_key = settings.deepseek_api_key
    if not api_key:
        return None
    try:
        balance = await fetch_balance(api_key)
    except Exception:
        logger.warning("could not read the upstream balance", exc_info=True)
        return None
    floor = settings.deepseek_balance_alert_cny
    if not balance.available:
        alerting.send(
            "DeepSeek 余额不足，调用已经被拒",
            [
                f"余额 {balance.total:g} {balance.currency}"
                f"（充值 {balance.topped_up:g} / 赠送 {balance.granted:g}）",
                "上游拒绝后所有项目一起停，补团队额度没有用；去 DeepSeek 充值。",
            ],
            key=ALERT_KEY,
        )
    elif balance.total < floor:
        alerting.send(
            "DeepSeek 余额快见底了",
            [
                sentence(balance, floor),
                f"充值 {balance.topped_up:g} / 赠送 {balance.granted:g}；"
                "用完之前充值，否则所有项目一起停。",
            ],
            key=ALERT_KEY,
        )
    return balance
