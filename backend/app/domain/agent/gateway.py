"""LiteLLM gateway ADMIN client. **This docstring is where L1/L2 are defined** —
the rest of the codebase refers to them by pointing here.

Three layers, of which this module is the top two. **L0** is the pool merely
routing through the self-hosted gateway, which needs nothing from this file.
On top of it, this client gives cheesex the two things the hooks backends
cannot get locally:

- **L1 attribution + metering**: mint a per-project VIRTUAL key (the sandbox
  gets that instead of the master key — a sandbox never holds admin credentials)
  and read real token usage back from the gateway's daily totals per key.
- **L2 budget brake**: set the key's ``max_budget`` from the project's compute
  grants so the gateway refuses further calls once the budget is spent.

Metering model: per-project usage is drained as a **daily cumulative delta per
model** — read today's totals for the project's virtual KEY (LiteLLM keeps
them per key, keyed by ``api_key`` = sha256 of the key, and per model under the
name its spend-log rows carry), and consume each difference against the stored
checkpoint. Cumulative sums are monotone, so a delta is consumed exactly once
even when LiteLLM records a call late (it simply enlarges a later drain).
Each drain finalizes every day since its checkpoint once per model, then
starts today. **The per-model split is not
optional**: summing the models and stamping one name attributes a mixed day to
whatever model the caller happened to hold, which is how mimo went uncounted
on the dashboard.

Everything here is best-effort: a gateway/admin failure logs and returns an
explicit unknown result — it must never fail a turn, but it must not be mistaken
for an authoritative zero either. Pure HTTP + arithmetic; persistence of the
minted key/checkpoint lives with the caller (project.settings).
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 60.0


@dataclass(frozen=True)
class GatewayModel:
    """One model the gateway will route, as the gateway itself reports it.

    ``selectable`` and ``priced`` are the two things cheese needs that routing
    alone does not answer — see ``LlmGateway.models``."""

    id: str
    label: str
    selectable: bool
    priced: bool
    # The model's tier (``cheese_tier``): which plans may use it.
    tier: str = "included"
    # False when the route loses the system-role messages Claude Code puts in
    # the middle of a conversation (``supports_mid_conversation_system: false``,
    # set by hand after measuring). Unmarked routes keep Claude Code's default.
    mid_conversation_system: bool = True
    # The thinking efforts this model honours (``cheese_efforts``), in the
    # platform's own words (``EFFORTS``). Empty: it takes none, and a teammate
    # on it runs at the model's own default.
    efforts: tuple[str, ...] = ()


#: The thinking efforts a teammate can ask for, lowest first. Claude Code
#: passes the one chosen to the model as ``output_config.effort``; whether the
#: model honours it, and which of them, is the gateway's to declare per model.
EFFORTS = ("low", "medium", "high", "max")


def declared_efforts(value: object) -> tuple[str, ...]:
    """The efforts a ``cheese_efforts`` entry declares, in ``EFFORTS`` order.
    Anything else in it is ignored rather than offered: a word the platform
    does not know is one it cannot pass on."""
    if not isinstance(value, list):
        return ()
    return tuple(effort for effort in EFFORTS if effort in value)


def price_is_set(*sources: object) -> bool:
    """Will the gateway bill this model at a non-zero rate?

    Both directions must carry a rate. A model priced on one side only bills
    half its traffic at zero, which is the same silent-brake failure as no price
    at all, arriving at half speed.

    Two sources because a deployment may put the rates in either place:
    LiteLLM reads ``input_cost_per_token`` from ``litellm_params`` AND from
    ``model_info``, and this repo's own gateway config uses both (the GLM
    entries the former, ``deepseek-flash`` the latter). Reading one would
    report every GLM model as unpriced and drop it from the catalogue.
    """

    def rate(field: str) -> float:
        for src in sources:
            if not isinstance(src, dict):
                continue
            value = src.get(field)
            if isinstance(value, int | float) and not isinstance(value, bool):
                if value > 0:
                    return float(value)
        return 0.0

    return bool(rate("input_cost_per_token") and rate("output_cost_per_token"))


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

    async def models(self) -> list[GatewayModel] | None:
        """Every model the gateway is routing right now, from ``/model/info``.

        This is the ONE place a gateway model is declared: the gateway needs its
        route and price to serve it at all, so a second list in cheese could
        only ever drift out of step with it, and nothing would report the
        disagreement — a model dropped here stays in the picker, and the person
        who selects it finds out when their turn fails to route.

        Returns ``None`` when the gateway cannot be asked. That is NOT an empty
        catalogue: the caller must keep serving what it last knew (see
        ``gateway_catalog``), because a picker that empties on a network blip
        stops every agent on the deployment.

        Reads the response LiteLLM documents at ``/model/info``: ``data`` rows of
        ``model_name`` + ``model_info`` + ``litellm_params`` (credentials already
        stripped gateway-side). These keys under ``model_info`` are cheese's,
        all optional metadata LiteLLM passes through untouched:

          - ``cheese_selectable``: offer this to people. Opt-in, because the
            gateway also routes models that are NOT menu items — ``glm-4.5``
            is where the subagent alias points, and listing it would invite
            someone to pick a model we route to on their behalf.
          - ``cheese_label``: what to call it; the id when absent.
          - ``cheese_efforts``: the thinking efforts it honours (``EFFORTS``).

        One LiteLLM key is read as well: ``supports_mid_conversation_system``
        set to ``false`` marks a route that loses mid-conversation system
        messages, and Claude Code is launched without them on it.

        A model the gateway reports as ``blocked`` is never selectable, however
        ``cheese_selectable`` is set: the gateway refuses to route it, so
        offering it would hand someone a route that cannot be called.
        """
        try:
            async with self._client() as client:
                r = await client.get(f"{self._base}/model/info", headers=self._headers)
                r.raise_for_status()
                payload = r.json()
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise ValueError("gateway /model/info carries no data list")
        except Exception:  # noqa: BLE001 — an unreachable gateway is not an empty one
            logger.warning("gateway models failed", exc_info=True)
            return None

        out: list[GatewayModel] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("model_name")
            if not isinstance(name, str) or not name or name in seen:
                continue
            seen.add(name)
            info = row.get("model_info")
            params = row.get("litellm_params")
            info = info if isinstance(info, dict) else {}
            label = info.get("cheese_label")
            tier = info.get("cheese_tier")
            out.append(
                GatewayModel(
                    id=name,
                    label=label if isinstance(label, str) and label else name,
                    selectable=(
                        info.get("cheese_selectable") is True
                        and info.get("blocked") is not True
                    ),
                    priced=price_is_set(params, info),
                    tier=tier if isinstance(tier, str) and tier else "included",
                    mid_conversation_system=(
                        info.get("supports_mid_conversation_system") is not False
                    ),
                    efforts=declared_efforts(info.get("cheese_efforts")),
                )
            )
        return out

    async def mint_project_key(
        self, project_id: uuid.UUID, *, platform: bool = False
    ) -> str | None:
        """Create a project-scoped virtual key (no expiry; attributed via
        ``user_id``). The caller persists it in project.settings — minting is
        NOT idempotent on the gateway side, so mint once and store.

        ``platform`` mints the project's second key, the one the platform's own
        work in it (memory consolidation) runs on: its spend is the
        platform's, so it must not mix into the key the project pays for."""
        alias = f"project-{project_id}" + ("-platform" if platform else "")
        metadata = {"cheesex_project": str(project_id)}
        if platform:
            metadata["purpose"] = "platform"
        try:
            async with self._client() as client:
                r = await client.post(
                    f"{self._base}/key/generate",
                    headers=self._headers,
                    json={
                        "key_alias": alias,
                        "user_id": project_user_id(project_id),
                        "metadata": metadata,
                    },
                )
                r.raise_for_status()
                key = r.json().get("key")
                return key if isinstance(key, str) and key else None
        except Exception:  # noqa: BLE001 — admin failures never fail a turn
            logger.warning("gateway mint_project_key failed", exc_info=True)
            return None

    async def mint_person_key(self, user_id: int, model: str) -> str | None:
        """Create a person's virtual key for their 芝士: attributed to them, and
        good for ``model`` alone, so a credential that reaches it can spend on
        nothing else. No budget on the gateway — what it spends is charged to
        the person's credits. Not idempotent either; the caller stores it."""
        try:
            async with self._client() as client:
                r = await client.post(
                    f"{self._base}/key/generate",
                    headers=self._headers,
                    json={
                        "key_alias": f"user-{user_id}",
                        "user_id": f"user:{user_id}",
                        "models": [model],
                        "metadata": {"cheesex_user": user_id, "purpose": "assistant"},
                    },
                )
                r.raise_for_status()
                key = r.json().get("key")
                return key if isinstance(key, str) and key else None
        except Exception:  # noqa: BLE001 — the person is told 芝士 is unavailable
            logger.warning("gateway mint_person_key failed", exc_info=True)
            return None

    async def revoke_key(self, key: str) -> bool:
        """Delete a virtual key on the gateway, so nothing can call on it."""
        try:
            async with self._client() as client:
                r = await client.post(
                    f"{self._base}/key/delete",
                    headers=self._headers,
                    json={"keys": [key]},
                )
                r.raise_for_status()
                return True
        except Exception:  # noqa: BLE001 — the caller says what was left
            logger.warning("gateway revoke_key failed", exc_info=True)
            return False

    async def set_key_budget(self, key: str, max_budget_usd: float | None) -> bool:
        """L2: cap the key's lifetime spend; the gateway rejects calls past it.
        ``None`` clears the cap."""
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

    async def daily_spend_by_model(
        self, key: str, date: str
    ) -> dict[str, "ModelSpend"] | None:
        """Cumulative tokens/spend for one virtual KEY on one UTC day, **split
        by model**.

        Read from LiteLLM's daily totals (`/user/daily/activity`), which it
        keeps per key, model and UTC day from the same rows as its spend log.
        ``breakdown.models`` names models the way a spend-log row does, which
        is what checkpoints already carry; ``model_groups`` uses other names.
        Only this key's share of each model is counted, so a gateway that
        ignored the ``api_key`` filter could not bill the project for another
        key's spend.

        Summing the spend log row by row instead downloaded every row of the
        day at each read: on dev on 2026-10-04 the busiest key had 25,045
        rows, 238 MB that took 54 s, decoded on the event loop at the end of
        every turn. The daily totals were 18.5 kB, read in 66 ms, and equal to
        that sum for every model.

        Returns ``None`` when the gateway cannot be asked — NOT an empty dict.
        An unreachable gateway is unknown spend, not zero spend, and the
        caller must preserve its checkpoint.
        """
        hashed = hashlib.sha256(key.encode()).hexdigest()
        by: dict[str, ModelSpend] = {}
        try:
            async with self._client() as client:
                r = await client.get(
                    f"{self._base}/user/daily/activity",
                    headers=self._headers,
                    params={
                        "api_key": hashed,
                        "start_date": date,
                        "end_date": date,
                        "page_size": 1000,
                    },
                )
                r.raise_for_status()
                body = r.json()
            results = body.get("results") if isinstance(body, dict) else None
            if not isinstance(results, list):
                raise ValueError("gateway daily activity has no results list")
            for day in results:
                if not isinstance(day, dict) or day.get("date") != date:
                    continue
                models = (day.get("breakdown") or {}).get("models") or {}
                for name, entry in models.items():
                    mine = ((entry or {}).get("api_key_breakdown") or {}).get(hashed)
                    if not isinstance(mine, dict):
                        continue
                    metrics = mine.get("metrics") or {}
                    _add_row(by, {**metrics, "model": name})
        except Exception:  # noqa: BLE001
            logger.warning("gateway daily_spend_by_model failed", exc_info=True)
            return None
        return by

    async def daily_spend(self, key: str, date: str) -> DailySpend | None:
        """Day total (sum over models). Use ``daily_spend_by_model`` when the
        question is 「which model」 — this aggregate only answers 「how much」."""
        by = await self.daily_spend_by_model(key, date)
        if by is None:
            return None
        return DailySpend(
            date=date,
            prompt_tokens=sum(m.prompt_tokens for m in by.values()),
            completion_tokens=sum(m.completion_tokens for m in by.values()),
            spend_usd=sum(m.spend_usd for m in by.values()),
        )


@dataclass(frozen=True)
class ModelSpend:
    """One model's tokens/spend — either a day total or a drained delta.

    ``model == ""`` means "counted but not attributed": either the spend-log
    row carried no model, or this is the one migration drain from a pre-split
    checkpoint (see ``drain_new_usage``). The caller stamps a display name on
    that case; it must not invent a model.
    """

    model: str
    prompt_tokens: int
    completion_tokens: int
    spend_usd: float


def _add_row(by: dict[str, ModelSpend], row: dict) -> None:
    """Add one model's tokens and spend to its running total."""
    name = str(row.get("model") or "")
    prev = by.get(name) or ModelSpend(name, 0, 0, 0.0)
    by[name] = ModelSpend(
        model=name,
        prompt_tokens=prev.prompt_tokens + int(row.get("prompt_tokens") or 0),
        completion_tokens=prev.completion_tokens
        + int(row.get("completion_tokens") or 0),
        spend_usd=prev.spend_usd + float(row.get("spend") or 0.0),
    )


def utc_today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _prior_days(checkpoint_date: str, today: str) -> list[str] | None:
    """Every unfinalized UTC day, including the checkpoint day."""
    first = date.fromisoformat(checkpoint_date)
    current = date.fromisoformat(today)
    if first > current:
        logger.warning("gateway checkpoint is in the future; preserving checkpoint")
        return None
    days = []
    while first < current:
        days.append(first.isoformat())
        first += timedelta(days=1)
    return days


def _snapshot(by: dict[str, ModelSpend]) -> dict[str, dict]:
    """Day totals as the JSON-able checkpoint body (project.settings)."""
    return {
        m.model: {
            "prompt": m.prompt_tokens,
            "completion": m.completion_tokens,
            "spend_usd": m.spend_usd,
        }
        for m in by.values()
    }


def _prev_models(ckpt: dict | None) -> dict[str, ModelSpend]:
    raw = (ckpt or {}).get("models")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, ModelSpend] = {}
    for name, v in raw.items():
        if not isinstance(v, dict):
            continue
        key = str(name)
        out[key] = ModelSpend(
            model=key,
            prompt_tokens=int(v.get("prompt") or 0),
            completion_tokens=int(v.get("completion") or 0),
            spend_usd=float(v.get("spend_usd") or 0.0),
        )
    return out


def _deltas(
    cur: dict[str, ModelSpend], prev: dict[str, ModelSpend]
) -> list[ModelSpend]:
    """Per-model growth since ``prev``.

    A model that appears for the first time contributes its full total (there
    is nothing prior to subtract). A model that vanished contributes nothing —
    cumulative sums never shrink, so a missing name is the gateway having
    rotated its label, not spend being refunded. Negative components are
    clamped at zero and logged by the caller's regression check.
    """
    out: list[ModelSpend] = []
    for name, c in cur.items():
        p = prev.get(name) or ModelSpend(name, 0, 0, 0.0)
        row = ModelSpend(
            model=name,
            prompt_tokens=max(0, c.prompt_tokens - p.prompt_tokens),
            completion_tokens=max(0, c.completion_tokens - p.completion_tokens),
            spend_usd=max(0.0, c.spend_usd - p.spend_usd),
        )
        if row.prompt_tokens or row.completion_tokens or row.spend_usd:
            out.append(row)
    return out


#: How far a model's day of spend may read below its checkpoint and still be
#: the same total. Tokens are integers and compared exactly; spend is a float
#: that LiteLLM sums in its own order, and a checkpoint summed in another order
#: differs from it in the last bits (dev, 2026-10-04: 9.4626364 read against a
#: checkpoint of 9.462636400000003). Compared exactly, that held back every
#: charge of the project. A millionth of a cent is far below any real charge.
_SPEND_EPSILON_USD = 1e-9


def _regressed(cur: dict[str, ModelSpend], prev: dict[str, ModelSpend]) -> bool:
    """Has the cumulative read gone backwards — or lost a model we had?

    Either one means the read is not the whole picture (LiteLLM batch-writes
    its spend rows; a lagged or partial page is routine). Persisting an
    under-count as the new checkpoint would re-bill the difference on the next
    pass, so the caller must keep the old checkpoint and retry later.

    A model that had spend and now reports none is **not** a refund — the spend
    log is append-only and cumulative sums never shrink. Disappearance is
    therefore an incomplete read, same as the totals going backwards.
    """
    for name, p in prev.items():
        if not (p.prompt_tokens or p.completion_tokens or p.spend_usd):
            continue
        c = cur.get(name)
        if c is None:
            return True
        if (
            c.prompt_tokens < p.prompt_tokens
            or c.completion_tokens < p.completion_tokens
            or c.spend_usd < p.spend_usd - _SPEND_EPSILON_USD
        ):
            return True
    return False


async def drain_new_usage(
    gateway: LlmGateway, key: str, ckpt: dict | None
) -> tuple[list[ModelSpend], dict] | None:
    """Usage newly seen since ``ckpt``, **split by model**.

    Returns ``(new_rows, next_ckpt)``: one ``ModelSpend`` per model that grew
    (its fields are the DELTA, not the day total), and the checkpoint to
    persist in ``project.settings``. Checkpoint shape (v2)::

        {"date": "YYYY-MM-DD",
         "models": {"mimo-v2.6-pro":
                        {"prompt": 12, "completion": 3, "spend_usd": 0.01}}}

    Exactly-once is unchanged: cumulative per-model sums are monotone, so a
    delta is consumed once even when LiteLLM logs rows late (they enlarge a
    later drain). Day rollover finalizes every day since the checkpoint per
    model, then starts today from zero.

    **v1 checkpoints** (flat ``prompt``/``completion``/``spend_usd``, written
    before the split) can only yield an UNATTRIBUTED delta — the model split of
    what they already counted is gone, and inventing one would be fabrication.
    That single migration drain lands under the empty model name (the caller
    stamps the default, exactly as before this fix) and hands back a v2
    checkpoint, so every later drain is attributed.
    """
    today = utc_today()
    if ckpt is not None and "models" not in ckpt:
        return await _drain_pre_split(gateway, key, ckpt, today)

    prev_date = str(ckpt.get("date")) if ckpt else today
    prev = _prev_models(ckpt)

    days = _prior_days(prev_date, today)
    if days is None:
        return None
    new: list[ModelSpend] = []
    for day in days:
        # Finalize the checkpoint day, then every untouched intervening day.
        final = await gateway.daily_spend_by_model(key, day)
        if final is None:
            return None
        if day == prev_date and _regressed(final, prev):
            logger.warning(
                "gateway cumulative spend regressed; preserving checkpoint",
                extra={"date": day},
            )
            return None
        new.extend(_deltas(final, prev))
        prev = {}
    cur = await gateway.daily_spend_by_model(key, today)
    if cur is None:
        return None
    if prev_date == today and _regressed(cur, prev):
        logger.warning(
            "gateway cumulative spend regressed; preserving checkpoint",
            extra={"date": today},
        )
        return None
    new.extend(_deltas(cur, prev))
    return new, {"date": today, "models": _snapshot(cur)}


async def _drain_pre_split(
    gateway: LlmGateway, key: str, ckpt: dict, today: str
) -> tuple[list[ModelSpend], dict] | None:
    """One migration drain for a checkpoint written before models were split.

    See ``drain_new_usage``. The arithmetic is the old flat one on purpose: the
    checkpoint only ever recorded day totals, so the delta can only be stated
    as totals. It is returned under the empty model name and the checkpoint is
    upgraded to v2 from the per-model day totals, which stops the bleed at one
    mis-attributed drain instead of every drain forever.
    """
    prev_date = str(ckpt.get("date") or today)
    prev_p = int(ckpt.get("prompt") or 0)
    prev_c = int(ckpt.get("completion") or 0)
    prev_u = float(ckpt.get("spend_usd") or 0.0)

    new_p = new_c = 0
    new_u = 0.0
    days = _prior_days(prev_date, today)
    if days is None:
        return None
    for day in days:
        final = await gateway.daily_spend(key, day)
        if final is None:
            return None
        new_p += max(0, final.prompt_tokens - prev_p)
        new_c += max(0, final.completion_tokens - prev_c)
        new_u += max(0.0, final.spend_usd - prev_u)
        prev_p, prev_c, prev_u = 0, 0, 0.0
    cur = await gateway.daily_spend_by_model(key, today)
    if cur is None:
        return None
    tot_p = sum(m.prompt_tokens for m in cur.values())
    tot_c = sum(m.completion_tokens for m in cur.values())
    tot_u = sum(m.spend_usd for m in cur.values())
    if prev_date == today and (tot_p < prev_p or tot_c < prev_c or tot_u < prev_u):
        logger.warning(
            "gateway cumulative spend regressed; preserving checkpoint",
            extra={"date": today},
        )
        return None
    new_p += max(0, tot_p - prev_p)
    new_c += max(0, tot_c - prev_c)
    new_u += max(0.0, tot_u - prev_u)
    rows = (
        [
            ModelSpend(
                model="", prompt_tokens=new_p, completion_tokens=new_c, spend_usd=new_u
            )
        ]
        if (new_p or new_c or new_u)
        else []
    )
    return rows, {"date": today, "models": _snapshot(cur)}
