"""LiteLLM gateway ADMIN client (docs/llm-gateway.md L1/L2).

When the model pool routes through the self-hosted gateway (L0), this client
gives cheesex the two things the hooks backends can't get locally:

- **L1 attribution + metering**: mint a per-project VIRTUAL key (the sandbox
  gets that instead of the master key — a sandbox never holds admin credentials)
  and read real token usage back from ``/spend/logs``.
- **L2 budget brake**: set the key's ``max_budget`` from the project's compute
  grants so the gateway refuses further calls once the budget is spent.

Metering model: per-project usage is drained as a **daily cumulative delta** —
sum today's spend-log rows for the project's virtual KEY (rows are filtered by
``api_key`` = sha256 of the key; verified live — a key's ``user_id`` does not
reach the rows), consume the difference against the stored checkpoint.
Cumulative sums are monotone, so a delta is
consumed exactly once even when LiteLLM logs rows late (they simply enlarge a
later drain). Day rollover finalizes yesterday once, then starts today.

Everything here is best-effort: a gateway/admin failure logs and returns an
explicit unknown result — it must never fail a turn, but it must not be mistaken
for an authoritative zero either. Pure HTTP + arithmetic; persistence of the
minted key/checkpoint lives with the caller (project.settings).
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 8.0


@dataclass
class DailySpend:
    """One project's gateway spend for one UTC day (cumulative)."""

    date: str  # YYYY-MM-DD (UTC)
    prompt_tokens: int
    completion_tokens: int
    spend_usd: float


def project_user_id(project_id: uuid.UUID) -> str:
    """The LiteLLM ``user_id`` a project's calls are attributed to (set on the
    virtual key at mint time, so every request through that key carries it)."""
    return f"project:{project_id}"


class LlmGateway:
    """Thin admin-API client. One instance per process (deps). ``transport`` is
    a test seam (httpx.MockTransport); None = real network."""

    def __init__(
        self,
        base: str,
        admin_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base.rstrip("/")
        self._headers = {"Authorization": f"Bearer {admin_key}"}
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=_TIMEOUT, transport=self._transport)

    async def mint_project_key(self, project_id: uuid.UUID) -> str | None:
        """Create a project-scoped virtual key (no expiry; attributed via
        ``user_id``). The caller persists it in project.settings — minting is
        NOT idempotent on the gateway side, so mint once and store."""
        try:
            async with self._client() as client:
                r = await client.post(
                    f"{self._base}/key/generate",
                    headers=self._headers,
                    json={
                        "key_alias": f"project-{project_id}",
                        "user_id": project_user_id(project_id),
                        "metadata": {"cheesex_project": str(project_id)},
                    },
                )
                r.raise_for_status()
                key = r.json().get("key")
                return key if isinstance(key, str) and key else None
        except Exception:  # noqa: BLE001 — admin failures never fail a turn
            logger.warning("gateway mint_project_key failed", exc_info=True)
            return None

    async def set_key_budget(self, key: str, max_budget_usd: float) -> bool:
        """L2: cap the key's lifetime spend; the gateway rejects calls past it."""
        try:
            async with self._client() as client:
                r = await client.post(
                    f"{self._base}/key/update",
                    headers=self._headers,
                    json={"key": key, "max_budget": max_budget_usd},
                )
                r.raise_for_status()
                return True
        except Exception:  # noqa: BLE001
            logger.warning("gateway set_key_budget failed", exc_info=True)
            return False

    async def daily_spend(self, key: str, date: str) -> DailySpend | None:
        """Cumulative tokens/spend for one virtual KEY on one UTC day, from
        ``/spend/logs`` rows (shape verified on the deployed gateway:
        prompt_tokens / completion_tokens / spend per row). Filtered by
        ``api_key`` = sha256 of the raw key — the identifier LiteLLM stamps on
        rows (verified live; the key's ``user_id`` does NOT reach the rows,
        they carry ``user=default_user_id``)."""
        prompt = completion = 0
        usd = 0.0
        try:
            day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
            async with self._client() as client:
                r = await client.get(
                    f"{self._base}/spend/logs",
                    headers=self._headers,
                    params={
                        "api_key": hashlib.sha256(key.encode()).hexdigest(),
                        "start_date": date,
                        "end_date": (day + timedelta(days=1)).strftime("%Y-%m-%d"),
                        "summarize": "false",
                    },
                )
                r.raise_for_status()
                rows = r.json()
                if not isinstance(rows, list):
                    raise ValueError("gateway spend response is not a list")
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    prompt += int(row.get("prompt_tokens") or 0)
                    completion += int(row.get("completion_tokens") or 0)
                    usd += float(row.get("spend") or 0.0)
        except Exception:  # noqa: BLE001
            logger.warning("gateway daily_spend failed", exc_info=True)
            return None
        return DailySpend(
            date=date, prompt_tokens=prompt, completion_tokens=completion, spend_usd=usd
        )


def utc_today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


async def drain_new_usage(
    gateway: LlmGateway, key: str, ckpt: dict | None
) -> tuple[int, int, float, dict] | None:
    """Usage newly seen since ``ckpt`` (exactly-once via daily cumulative
    deltas). Returns ``(new_prompt, new_completion, new_spend_usd, next_ckpt)``;
    the caller persists ``next_ckpt`` (in project.settings). ``ckpt`` shape:
    ``{"date": "YYYY-MM-DD", "prompt": int, "completion": int, "spend_usd": float}``."""
    today = utc_today()
    prev_date = str(ckpt.get("date")) if ckpt else today
    prev_p = int(ckpt.get("prompt") or 0) if ckpt else 0
    prev_c = int(ckpt.get("completion") or 0) if ckpt else 0
    prev_usd = float(ckpt.get("spend_usd") or 0.0) if ckpt else 0.0

    new_p = new_c = 0
    new_usd = 0.0
    if prev_date != today:
        # Finalize the checkpoint day (catch rows logged after its last drain)…
        final = await gateway.daily_spend(key, prev_date)
        if final is None:
            return None
        new_p += max(0, final.prompt_tokens - prev_p)
        new_c += max(0, final.completion_tokens - prev_c)
        new_usd += max(0.0, final.spend_usd - prev_usd)
        prev_p, prev_c, prev_usd = 0, 0, 0.0  # …then start today from zero.
    cur = await gateway.daily_spend(key, today)
    if cur is None:
        return None
    if prev_date == today and (
        cur.prompt_tokens < prev_p
        or cur.completion_tokens < prev_c
        or cur.spend_usd < prev_usd
    ):
        logger.warning(
            "gateway cumulative spend regressed; preserving checkpoint",
            extra={"date": today},
        )
        return None
    new_p += max(0, cur.prompt_tokens - prev_p)
    new_c += max(0, cur.completion_tokens - prev_c)
    new_usd += max(0.0, cur.spend_usd - prev_usd)
    next_ckpt = {
        "date": today,
        "prompt": cur.prompt_tokens,
        "completion": cur.completion_tokens,
        "spend_usd": cur.spend_usd,
    }
    return new_p, new_c, new_usd, next_ckpt
