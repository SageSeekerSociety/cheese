"""Test fixtures.

DB-backed tests run on real PostgreSQL (the merged models need PG-native
JSONB/Sequence/ENUM that sqlite can't build; the schema is the alembic migrations).
FULL xdist isolation: every worker gets its OWN databases, so shared sequences /
reference rows / data never race across workers. Two DBs per worker because the
two harnesses can't share one:
  * ``cheesex_test[_<worker>]``    — the integration harness (per-test transactional
    rollback on a session-long connection); the app engines bind here.
  * ``cheesex_test[_<worker>]_c``  — client / python_client (TRUNCATE + a real
    session factory: ChatService spins up its own sessions and background turns
    COMMIT, which rollback can't isolate; truncate would also deadlock against the
    integration harness's open transaction, hence a separate DB).
A stub agent keeps tests off the live model.
"""

import asyncio
import os
import re
import sys
import time
import uuid
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Strip inherited git env. When the suite runs from the pre-commit HOOK it executes
# DURING `git commit`, which exports GIT_DIR / GIT_INDEX_FILE / GIT_WORK_TREE for
# the hook. The workspace tests (and the app's git ops) spawn `git` subprocesses;
# those vars take precedence over `git -C <tmprepo>` and would hijack them onto the
# MAIN repo — green when run directly, red only under the hook. Clear them so tests
# always get a clean, cwd-driven git context.
for _k in [k for k in os.environ if k.startswith("GIT_")]:
    del os.environ[_k]

# Client construction validates credentials before the mocked transport is used.
# Keep the suite hermetic instead of depending on a developer or CI secret.
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("ANTHROPIC_AUTH_TOKEN", "test-anthropic-token")

# Bind the app engine (app.core.db — the single pool; app.db.session re-exports
# it) to THIS worker's integration DB — must happen before any app import (the
# engine is built from settings.database_url at import time). -------------------
from app.core.config import settings  # noqa: E402
from tests import isolation  # noqa: E402

_XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")  # "gw0"… or "" (serial)
# The runner slot this run is on, empty everywhere but a pool machine with more
# than one. Two runs sharing a machine otherwise share every name below, and the
# harness drops its databases WITH (FORCE) — see tests/isolation.py.
_SLOT = os.environ.get("CHEESE_CI_SLOT", "")
_INTG_DB_NAME, _CLIENT_DB_NAME = isolation.database_names(_XDIST_WORKER, _SLOT)
# Postgres server root (no database). Defaults to the TEST server the repo-root
# docker-compose.yml publishes on :5433 (separate from the dev database on
# :5432, so a test run never touches what you were developing against). CI (and
# any other host) overrides it via TEST_PG_BASE so the per-worker databases are
# provisioned over the network instead of `docker exec`.
_PG_BASE = os.environ.get(
    "TEST_PG_BASE", "postgresql+asyncpg://postgres:postgres@localhost:5433"
)
settings.database_url = f"{_PG_BASE}/{_INTG_DB_NAME}"
# Redis needs the same per-worker split as Postgres. Its keys are scoped by
# user id (2FA secrets, backup codes, the #357 attempt budgets), and user ids
# restart from 1 in every worker's own database — so on ONE shared Redis, gw0's
# user 5 and gw1's user 5 are the same account. A 15-minute lockout earned by
# one worker would then land on an unrelated test in another, at whatever rate
# the two id sequences happen to align: the flakiest possible failure. Redis
# ships numbered databases; one per worker keeps them apart, and a slot takes a
# block of them so two runs sharing a machine cannot land on the same index.
_REDIS_BASE = re.sub(r"/\d*$", "", os.environ.get("REDIS_URL", settings.redis_url))
_REDIS_DB = isolation.redis_database(
    _XDIST_WORKER, int(os.environ.get("CHEESE_CI_REDIS_BASE_DB", "0"))
)
settings.redis_url = f"{_REDIS_BASE}/{_REDIS_DB}"
os.environ["REDIS_URL"] = settings.redis_url
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", f"{_PG_BASE}/{_CLIENT_DB_NAME}")
# Tests run many event loops per process; the shared app engine must not pool
# asyncpg connections across them (app.core.db reads this before building it).
os.environ["CHEESEX_TEST_NULLPOOL"] = "1"

import app.models  # noqa: F401, E402  (registers all tables on Base.metadata)
from app.api.deps import get_broker, get_chat_service, get_work_runner  # noqa: E402
from app.core.db import Base, get_db  # noqa: E402
from app.core.redis import get_redis_client  # noqa: E402
from app.core.sandbox_auth import SANDBOX_TOKEN  # noqa: E402
from app.domain.agent.chat import ChatService  # noqa: E402
from app.domain.agent.compute import ComputePool  # noqa: E402
from app.domain.agent.harness.channel import Channel  # noqa: E402
from app.domain.agent.harness.claude_code import (  # noqa: E402
    ClaudeCodeRuntime,
    HookRouter,
)
from app.domain.agent.harness.launch import LaunchPlan  # noqa: E402
from app.main import app  # noqa: E402

# Tests always run on the DB memory backend: the openviking backend holds an
# exclusive data-dir lock (owned by the dev server when it's running), and
# tests must not depend on — or corrupt — the live memory store.
settings.memory_backend = "db"
# Tests exercise the real authz enforcement regardless of the dev .env (which
# ships it OFF for the conservative dogfood rollout). Same leak class as the
# memory backend above: the .env value must not decide test behavior.
settings.authz_enforce_topic_access = True
# The `client` fixture enters lifespan, which starts every periodic job the
# platform runs (scheduler/jobs.py). Three of them would act on the test's own
# data behind its back: the email drain claims whatever a notification test
# queued and dead-letters it after three tries, the finalizer closes an
# aggregation window a test may be asserting is still open, and the deadline
# sweep flips a membership to FAILED. Zero is the same "not on this box" switch
# a deployment uses.
settings.notification_email_drain_interval_s = 0
settings.notification_finalize_interval_s = 0
settings.task_deadline_sweep_interval_s = 0


def _stranded_topics() -> set[str]:
    """Topics whose work can no longer move, so waiting on it is waiting forever.

    A turn is only ever finished by its own subscription's consumer, running on
    the loop that subscription was created on. Plenty of tests build their own
    ``ChatService``, run a turn on the test's loop, and return with hooks still
    queued — the loop closes, the consumer dies with it, and the turn stays
    marked in flight in the process-wide broker, which outlives all of it.

    That mark is indistinguishable from a real background turn by count alone,
    which is why waiting on the count alone used to cost thirty seconds a time.
    A closed loop is the difference: a consumer on one cannot run again, so
    whatever it was holding is not in flight, it is abandoned.
    """
    from app.domain.agent.harness.claude_code.hooks_substrate import (
        _RUNTIMES,
        ClaudeCodeRuntime,
    )

    stranded = set()
    for runtime in list(_RUNTIMES):
        if not isinstance(runtime, ClaudeCodeRuntime):
            continue
        for topic_id, subscription in list(runtime._subscriptions.items()):
            task = subscription.consumer_task
            if task is not None and task.get_loop().is_closed():
                stranded.add(str(topic_id))
    return stranded


def wait_work_idle() -> None:
    """Block until background turns (e.g. the 分身 kickoff a /split submits)
    finish: they run on the TestClient portal loop and write to this worker's DB —
    if a turn is still writing when the next test truncates, the test flakes.
    Returns as soon as they're idle; the generous ceiling only matters under heavy
    parallel/external load, when a turn can take much longer than usual.

    Abandoned work is skipped rather than waited out — see ``_stranded_topics``.
    Giving up is no longer SILENT either, and between them those two hid the
    suite's largest single cost for a long time: a test that stranded a turn paid
    the whole ceiling here and left no trace but a slower run. The first
    ``--durations`` report ever taken of this suite had seventeen of its twenty
    slowest entries in teardown, every one of them at ~30.5s.
    """
    runner = get_work_runner()
    for _ in range(3000):  # ~30s ceiling; returns early the instant turns drain
        active = set(runner._broker.active_channels())
        moving = len(runner._tasks) or len(active - _stranded_topics())
        if not moving:
            return
        time.sleep(0.01)
    print(
        f"\n[wait_work_idle] 等满 30 秒还有 {runner.active_work_count()} 份工作没收尾，"
        "本条测试为此付了 30 秒。留下的："
        f"{dict(runner._broker._active)}",
        file=sys.stderr,
    )


def retire_topic(client: TestClient, topic_id) -> None:
    """Put down a turn the test left running on purpose.

    A few tests drive a session that never reports Stop — that IS the scenario
    (a host that vanished mid-turn). The turn then stays in flight, correctly,
    and every one of them pays ``wait_work_idle``'s full ceiling on the way out
    for a turn nobody is waiting on any more.

    The subscription lives on the TestClient's portal loop, so the drop has to
    be made there rather than on whatever loop the test itself ran on.
    """
    from app.domain.agent.harness.claude_code import drop_topic_subscriptions

    client.portal.call(drop_topic_subscriptions, uuid.UUID(str(topic_id)))


class StubChannel(Channel):
    """A channel with no machine behind it.

    The turn flow tests exercise is the one production runs: the prompt is
    handed to a session and the reply comes back later through the hook
    subscription, not through the caller's iterator. So this stub supplies the
    only thing a real screen supplies — the hooks — and every layer above
    (assembly, attribution, receipts, turn close) is the real one.

    The four hooks are the vocabulary of a one-message turn. ``UserPromptSubmit``
    is not decoration: it is the receipt that stamps an injected message
    consumed, and without it mid-turn deliveries stay pending forever and replay.
    """

    name = "stub-hooks"

    def __init__(self, **timeouts: float) -> None:
        # Its own router: the module-global one is shared process-wide, and a
        # test that inherited another test's sink would read its hooks.
        self._router = HookRouter()
        # The runtime this channel is driven by. A channel and its runtime are
        # two objects in production and one fixture here, so the ~40 tests that
        # hand a screen around keep handing one thing around.
        # ``timeouts`` are the watchdog's (idle_suspect_s / hard_ceiling_s /
        # delivery_timeout_s), so a test about a session that goes quiet does
        # not have to wait the production fifteen minutes for it.
        self.runtime = ClaudeCodeRuntime(self, router=self._router, **timeouts)
        self.last_system_prompt: str | None = None
        self.last_resume_session_id: str | None = None
        self.last_prompt: str | None = None
        self.reply = "Hello world"
        # Fired the moment the transport actually writes, so a test can assert
        # what did (and did not) happen before the session was reached.
        self.on_start: Callable[[], None] | None = None

    async def ensure_ready(  # type: ignore[override]
        self,
        *,
        topic_id: uuid.UUID,
        launch: LaunchPlan,
        **_: object,
    ) -> uuid.UUID:
        self.last_system_prompt = launch.system_prompt
        self.last_resume_session_id = launch.resume_session_id
        return topic_id

    async def send_prompt(  # type: ignore[override]
        self, screen: uuid.UUID, prompt: str
    ) -> bool:
        self.last_prompt = prompt
        if self.on_start is not None:
            self.on_start()
        # AFTER this returns, never inside it. `send_prompt` is the transport
        # write; a screen that answered during it would collapse the whole
        # reason this contract separates feeding from reading, and would put
        # the reply ahead of frames the caller has not yielded yet.
        asyncio.get_running_loop().call_soon(self.emit_turn, screen, prompt, self.reply)
        return True

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        """The hooks a screen emits for one prompt it answered.

        Override this to script a different turn — a tool call between two
        messages, a subagent, silence. What must not change is the frame: a
        session announces itself, acknowledges the prompt, and stops. ``Stop``
        in particular is not optional: it is what closes the turn and publishes
        ``done``.
        """
        self.starts(topic_id)
        self.acknowledges(topic_id, prompt)
        self.says(topic_id, reply)
        self.stops(topic_id, reply)

    # --- the hooks, one method each ----------------------------------------

    def hook(self, topic_id: uuid.UUID, **payload: object) -> None:
        self._router.push(str(topic_id), dict(payload))

    def starts(self, topic_id: uuid.UUID, session_id: str = "sess-test-1") -> None:
        self.hook(topic_id, hook_event_name="SessionStart", session_id=session_id)

    def acknowledges(self, topic_id: uuid.UUID, prompt: str) -> None:
        """UserPromptSubmit — the receipt that stamps an injected message
        consumed. A session that never emits it leaves every mid-turn delivery
        pending, and pending messages are replayed (宁可重复不可丢失)."""
        self.hook(topic_id, hook_event_name="UserPromptSubmit", prompt=prompt)

    def says(self, topic_id: uuid.UUID, text: str) -> None:
        self.hook(topic_id, hook_event_name="MessageDisplay", delta=text)

    def uses(
        self,
        topic_id: uuid.UUID,
        name: str,
        *,
        eid: str | None = None,
        **tool_input: object,
    ) -> None:
        self.hook(
            topic_id,
            hook_event_name="PreToolUse",
            tool_name=name,
            tool_input=dict(tool_input),
            _eid=eid,
        )

    def returns(
        self,
        topic_id: uuid.UUID,
        name: str,
        response: object,
        *,
        eid: str | None = None,
        **tool_input: object,
    ) -> None:
        self.hook(
            topic_id,
            hook_event_name="PostToolUse",
            tool_name=name,
            tool_response=response,
            tool_input=dict(tool_input),
            _eid=eid,
        )

    def stops(
        self,
        topic_id: uuid.UUID,
        text: str,
        session_id: str = "sess-test-1",
        **extra: object,
    ) -> None:
        self.hook(
            topic_id,
            hook_event_name="Stop",
            session_id=session_id,
            last_assistant_message=text,
            usage={
                "model": "stub",
                "input_tokens": 10,
                "output_tokens": 5,
                "cost_usd": 0.001,
            },
            **extra,
        )


# Captured at import: a test that squeezes a production wait patches
# `asyncio.sleep` on the shared module, and a poller using the patched one
# never yields — it spins its whole budget without letting the consumer run.
_REAL_SLEEP = asyncio.sleep


async def drain_hooks(screen: StubChannel, topic_id: uuid.UUID) -> None:
    """Wait until every hook this screen pushed has been consumed.

    Narrower than `settle_turn`, and the right one when the turn is not going
    to end: it asks whether what the session already said has landed, not
    whether the session is done saying things.
    """
    subscription = screen.runtime._subscriptions.get(topic_id)
    if subscription is not None:
        await subscription.sink.queue.join()


async def settle_turn(service, topic_id, *, tries: int = 2000) -> None:
    """Wait until the session's hooks have been consumed and the turn closed.

    `converse()` returns as soon as the prompt is in the session — the reply
    arrives later, on the subscription. A test that drives the service directly
    (rather than through the runner and a socket) has to wait for that, the
    same way a room does.
    """
    for _ in range(tries):
        if not any(t == topic_id for t, _ in service._hook_work):
            return
        await _REAL_SLEEP(0.01)
    raise AssertionError(
        f"turn on {topic_id} never closed; open work: {list(service._hook_work)}"
    )


def stub_compute(channel: StubChannel | None = None) -> ComputePool:
    """A pool holding one screen, for the many tests that build a ChatService
    by hand. Pass the channel when the test asserts against it."""
    screen = channel or StubChannel()
    return ComputePool([screen.runtime], screen.name)


@pytest.fixture
def stub_hooks() -> StubChannel:
    # Per test: its subscriptions and consumer tasks live on the TestClient's
    # portal loop, which goes away with the client.
    return StubChannel()


@pytest.fixture
def bearer() -> Callable[[str], dict[str, str]]:
    """``Authorization`` headers proving the caller is ``handle``.

    Most 2.0 routes still accept a handle named in the body (Phase-0), but the
    ones that decide who may reach the project — writing the project roster —
    read the actor from the verified token only. Those tests need a real token.
    """

    def _headers(handle: str) -> dict[str, str]:
        # Deferred import: the shared helper lives in the integration conftest,
        # and only integration tests request this fixture. Minting here instead
        # would be a second source of truth for the same token —
        # tests/unit/test_no_adhoc_auth_helpers.py exists to stop exactly that.
        from tests.integration.conftest import session_auth_headers

        return session_auth_headers(handle)

    return _headers


@pytest.fixture(autouse=True)
def _redis_client_per_loop() -> Iterator[None]:
    """No test may inherit the redis client another test built.

    ``get_redis_client`` is ``@lru_cache``d, and in production that is right —
    one process, one event loop, one pool. Under pytest every test runs on a
    fresh loop, so a cached client carries the previous test's dead loop into
    this one and the first ``await`` on it raises "attached to a different
    loop". The test that pays is whichever one next touches redis, never the
    one that cached the client, so the failure arrives as an unrelated 500 in a
    file that passes in isolation.

    Autouse and here rather than in the files that noticed: a test file that
    clears the cache for itself buys its own tests immunity and leaves the leak
    for everyone downstream — which is precisely how this survived a release
    with one test exposed and fifteen around it green.
    """
    get_redis_client.cache_clear()
    yield
    get_redis_client.cache_clear()


@pytest.fixture
def client(_pg_schema, stub_hooks: StubChannel, tmp_path) -> Iterator[TestClient]:
    # Real PostgreSQL (not sqlite): the merged models use PG-native JSONB,
    # Sequences and ENUM types that sqlite's compiler can't render, and the schema
    # is defined by the alembic migrations (create_all can't build the pg ENUMs).
    # NullPool → every connection is created fresh in its calling event loop, so
    # the TestClient's portal loop and this fixture's setup loop never share an
    # asyncpg connection (which is loop-bound). Isolation is per-test TRUNCATE.
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    test_factory = async_sessionmaker(engine, expire_on_commit=False)

    asyncio.run(_truncate_all(engine))
    get_broker().reset()  # channel ids reset with the DB; drop stale buffered frames

    # agent-as-user baseline (P1): 芝士 is a real user with a platform agent-
    # binding — seeded by the migration in prod, re-seeded here after the truncate.
    async def _seed_agent_user() -> None:
        from app.domain.identity.services import IdentityService

        async with test_factory() as session:
            await IdentityService(session).ensure_agent_user()
            await session.commit()

    asyncio.run(_seed_agent_user())

    async def override_get_db():
        async with test_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    chat_service = ChatService(
        session_factory=test_factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([stub_hooks.runtime], stub_hooks.name),
    )

    def override_get_chat_service() -> ChatService:
        # ONE instance, like production's lru_cache. A per-request instance was
        # harmless while a turn was self-contained; it is not now that the reply
        # arrives on a subscription owned by the service that started the turn.
        return chat_service

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_chat_service] = override_get_chat_service

    with TestClient(app) as c:
        # The cheese write-API is token-gated (app.main.cheese_token_gate); send
        # the secret on every test request so contract tests exercising those
        # endpoints (doc/split/decision/...) aren't rejected with 401.
        c.headers["X-Cheese-Token"] = SANDBOX_TOKEN
        # Expose the factory so tests can seed data (e.g. memory entries).
        c.test_factory = test_factory  # type: ignore[attr-defined]
        yield c
        # Drain background turns BEFORE leaving the TestClient context:
        # disposing the engine under a running kickoff turn makes flakes.
        wait_work_idle()

    app.dependency_overrides.clear()
    asyncio.run(engine.dispose())


# --- PostgreSQL test-DB plumbing (per-worker, see the module docstring) --------


async def _truncate_all(engine) -> None:
    """Wipe every table for a clean per-test slate (fast; keeps the schema).

    TRUNCATE takes an exclusive lock on every table, so a session some earlier
    test left ``idle in transaction`` — holding no more than a share lock on one
    of them — makes it wait, and the server's ``lock_timeout`` is 0, so it waits
    forever. That used to surface as a 300 s pytest-timeout on the NEXT test's
    setup, then on the one after that, and the report named the victims and
    never the session holding the lock (see #693's sibling: the hang on
    ``test_runtime``/``test_work_continuation`` in CI, 2026-09-05). So the wait
    is bounded here, and when it runs out the error says who is in the way.
    """
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    if not tables:
        return
    try:
        async with engine.begin() as conn:
            await conn.exec_driver_sql("SET LOCAL lock_timeout = '20s'")
            await conn.exec_driver_sql(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
    except DBAPIError as exc:
        if "lock timeout" not in str(exc).lower():
            raise
        # The connection that timed out is done for (aborted transaction), so
        # the look-around runs on a fresh one.
        async with engine.connect() as probe:
            rows = await probe.exec_driver_sql(
                "select pid, state, now()-xact_start as xact_age,"
                " now()-state_change as since_change, left(query, 200) as query"
                " from pg_stat_activity"
                " where datname = current_database() and pid <> pg_backend_pid()"
                "   and state <> 'idle'"
                " order by xact_start"
            )
            others = [dict(r._mapping) for r in rows]
        lines = "\n".join(
            f"  pid={r['pid']} state={r['state']!r} xact_age={r['xact_age']}"
            f" since_change={r['since_change']}\n    query: {r['query']}"
            for r in others
        )
        raise RuntimeError(
            "TRUNCATE waited 20 s for a table lock. Another session on this"
            " worker's client database still holds one — most likely a test that"
            " left a transaction open. Sessions on the database right now:\n"
            + (lines or "  (none — the blocker went away as this was raised)")
        ) from exc


async def _admin_recreate_db(db_name: str) -> None:
    """Drop + recreate a database over the network (no `docker exec`), from the
    maintenance `postgres` DB (can't drop a DB you're connected to). FORCE closes
    any stale connection. asyncpg runs each statement in autocommit, which
    DROP/CREATE DATABASE require (they can't run inside a transaction)."""
    import asyncpg

    # asyncpg wants a plain libpq DSN, not the SQLAlchemy "+asyncpg" dialect URL.
    dsn = _PG_BASE.replace("+asyncpg", "") + "/postgres"
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()


def _migration_fingerprint() -> str:
    """Identity of the migration history, so a template built from an older one
    is never reused."""
    import hashlib
    from pathlib import Path

    versions = Path(__file__).resolve().parent.parent / "alembic" / "versions"
    digest = hashlib.sha256()
    for path in sorted(versions.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


_TEMPLATE_DB = f"cheesex_tpl_{_migration_fingerprint()}"


async def _db_exists(db_name: str) -> bool:
    import asyncpg

    dsn = _PG_BASE.replace("+asyncpg", "") + "/postgres"
    conn = await asyncpg.connect(dsn)
    try:
        return bool(
            await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name)
        )
    finally:
        await conn.close()


async def _clone_db(db_name: str, template: str) -> None:
    import asyncpg

    dsn = _PG_BASE.replace("+asyncpg", "") + "/postgres"
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{db_name}" TEMPLATE "{template}"')
    finally:
        await conn.close()


async def _drop_superseded_templates() -> None:
    """Remove every `cheesex_tpl_*` but this migration history's own.

    Never raises: a template that cannot be dropped (another run is cloning from
    it this second) is not this run's problem, and the next build tries again.
    """
    import asyncpg

    dsn = _PG_BASE.replace("+asyncpg", "") + "/postgres"
    conn = await asyncpg.connect(dsn)
    try:
        stale = await conn.fetch(
            "SELECT datname FROM pg_database"
            " WHERE datname LIKE 'cheesex_tpl_%' AND datname <> $1",
            _TEMPLATE_DB,
        )
        for row in stale:
            try:
                await conn.execute(f'DROP DATABASE IF EXISTS "{row["datname"]}"')
            except Exception:  # noqa: BLE001, PERF203 — in use is not an error here
                continue
    finally:
        await conn.close()


async def _rename_db(old: str, new: str) -> None:
    import asyncpg

    dsn = _PG_BASE.replace("+asyncpg", "") + "/postgres"
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{new}" WITH (FORCE)')
        await conn.execute(f'ALTER DATABASE "{old}" RENAME TO "{new}"')
    finally:
        await conn.close()


def _ensure_template() -> bool:
    """Build (once per machine, per migration history) a migrated template other
    databases are copied from. Returns False if anything went wrong, so the
    caller can fall back to migrating directly.

    Every xdist worker used to replay the entire history into its own database:
    a dozen concurrent transactions each creating ~90 tables with their indexes
    exhausts Postgres's preallocated lock table, and the server refuses with
    "out of shared memory" — which killed every worker's migration and errored
    the whole session before a single test ran. A service container cannot be
    given a larger lock table (no way to pass server arguments), so the fix is
    to stop asking for that many locks: migrate once, then clone.

    The template is built under a temporary name and renamed on success, so its
    existence means "complete" — a run killed mid-migration leaves the failed
    build behind, not a half-migrated template that later runs would trust.

    Building a new one also drops the templates of migration histories that are
    no longer current. That used to take care of itself, because the server was a
    container thrown away with the job; on a CI machine's resident Postgres they
    would accumulate instead, and its data directory is a 3 GB tmpfs.
    """
    import fcntl
    import tempfile
    from pathlib import Path

    lock_path = Path(tempfile.gettempdir()) / f"{_TEMPLATE_DB}.lock"
    try:
        with open(lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if asyncio.run(_db_exists(_TEMPLATE_DB)):
                return True
            building = f"{_TEMPLATE_DB}_building"
            _migrate_fresh_db(building, f"{_PG_BASE}/{building}")
            asyncio.run(_rename_db(building, _TEMPLATE_DB))
            asyncio.run(_drop_superseded_templates())
            return True
    except Exception:  # noqa: BLE001 — fall back to the slow path, never block
        return False


def _migrate_fresh_db(db_name: str, db_url: str) -> None:
    """Drop + recreate a database and migrate it to head (alembic)."""
    import subprocess
    import sys
    from pathlib import Path

    backend_dir = Path(__file__).resolve().parent.parent
    asyncio.run(_admin_recreate_db(db_name))
    # DATABASE_URL maps to settings.database_url, which alembic/env.py reads.
    # `python -m alembic` works from any host (local venv or CI) without assuming
    # a `.venv/bin/alembic` path.
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_dir,
        env={**os.environ, "DATABASE_URL": db_url},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # check=True raises with only the argv, so a migration failure reached
        # the log as "returned non-zero exit status 1" and nothing else — every
        # test in the session then errors with no way to tell a wedged Postgres
        # from a genuinely broken migration. Surface alembic's own words.
        raise RuntimeError(
            f"alembic upgrade head failed for {db_name} (rc={result.returncode})\n"
            f"--- stdout ---\n{result.stdout.strip()}\n"
            f"--- stderr ---\n{result.stderr.strip()}"
        )


def _create_and_migrate(db_name: str, db_url: str) -> None:
    """This worker's database, at head — cloned from the shared template when
    one could be built, migrated directly otherwise."""
    if _TEMPLATE_READY:
        asyncio.run(_clone_db(db_name, _TEMPLATE_DB))
        return
    _migrate_fresh_db(db_name, db_url)


# Built lazily by the first worker to reach the fixture; the rest clone it.
_TEMPLATE_READY = False


# tests/unit/ is the only tree allowed to run without a Postgres; everything
# else is DB-backed by construction. Trailing sep so a sibling like
# "tests/unittools/" can't match by prefix.
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_UNIT_DIR = os.path.join(_TESTS_DIR, "unit") + os.sep
_CONTRACT_DIR = os.path.join(_TESTS_DIR, "contract") + os.sep


def _needs_db(request: pytest.FixtureRequest) -> bool:
    """Whether this test requires the Postgres schema to be provisioned.

    Two independent reasons, because fixture names alone aren't enough: the
    integration harness binds ``settings.database_url`` directly (module import
    time), so a test there can touch the DB without naming a DB fixture. Hence
    anything outside ``tests/unit/`` is assumed DB-backed. Inside ``tests/unit/``
    we go by the fixture closure — ``request.fixturenames`` is transitive, and
    every DB-bound fixture (``client``, ``python_client``, integration's
    ``db_connection``/``db_session``…) chains to ``_pg_schema``, so requesting any
    of them shows up here.
    """
    return (
        not str(request.path).startswith(_UNIT_DIR)
        or "_pg_schema" in request.fixturenames
    )


# The three layers the suite runs as. CI runs one pytest per layer, in series,
# each with its own ceiling, so a wedge or a slowdown names the layer it is in
# instead of arriving as one number for 6800 tests.
_LAYERS = frozenset({"pure", "contract", "integration"})


def _layer_of(item: pytest.Item) -> str:
    """Which layer ``item`` runs in.

    ``pure`` is the layer that runs on a machine with no Postgres, so what
    decides it is the fixture closure and not the directory: every DB-bound
    fixture chains to ``_pg_schema`` (see ``_needs_db`` above), and
    ``fixturenames`` is that closure, autouse and transitive included. A file
    under ``tests/unit/`` that does name one — the turn log lives in Postgres,
    so the runner's tests do — is a database test wherever it sits, and runs in
    the integration step with a database under it.
    """
    path = os.fspath(item.path) if item.path is not None else ""
    if path.startswith(_CONTRACT_DIR):
        return "contract"
    # getattr: only a Function has a fixture closure, and a collected node that
    # has none has not asked for a database either.
    closure = getattr(item, "fixturenames", ())
    if path.startswith(_UNIT_DIR) and "_pg_schema" not in closure:
        return "pure"
    return "integration"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Give every collected test exactly one layer marker.

    The three CI steps are the whole suite only if every test carries one and
    only one of the three markers: a test carrying none runs in no step and is
    reported nowhere — green CI over code nothing checked — and one carrying two
    is counted, and timed, twice. Neither can be seen in a passing run, which is
    why this fails the collection rather than warning.

    Derived here rather than written on each test: a marker on the test is a
    second declaration of what its fixture list already says, and the two drift
    the moment a test grows a database and nobody moves its marker.
    """
    misfiled = []
    for item in items:
        declared = {m.name for m in item.iter_markers()} & _LAYERS
        if declared:
            misfiled.append(
                f"  {item.nodeid}\n"
                f"    carries {sorted(declared)} already — the layer is derived"
                f" from the fixture closure, so remove the marker"
            )
            continue
        item.add_marker(_layer_of(item))
    if misfiled:
        raise pytest.UsageError(
            "These tests declare their own layer:\n" + "\n".join(misfiled)
        )


async def _terminate_open_transactions(db_name: str) -> list[dict]:
    """Sessions left ``idle in transaction`` on ``db_name``, each terminated
    after being recorded. Runs on the maintenance database so it can see and
    end them regardless of which loop created them."""
    import asyncpg

    dsn = _PG_BASE.replace("+asyncpg", "") + "/postgres"
    conn = await asyncpg.connect(dsn, timeout=10)
    try:
        rows = await conn.fetch(
            "select pid, now()-xact_start as xact_age, left(query, 200) as query"
            " from pg_stat_activity"
            " where datname = $1 and backend_type = 'client backend'"
            "   and state = 'idle in transaction'",
            db_name,
        )
        found = [dict(r) for r in rows]
        for r in found:
            await conn.execute("select pg_terminate_backend($1)", r["pid"])
        return found
    finally:
        await conn.close()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item: pytest.Item):
    """After a client-DB test has torn down, no session of ours may still be
    inside a transaction on that database.

    One left open holds locks the next test's TRUNCATE needs (see
    ``_truncate_all``), and it is invisible from there: the report names the
    test that waited, never the one that leaked. Checking at the leaker's own
    teardown is what pins it, so a leak fails HERE, on the test that made it,
    with the statement it was running. The session is terminated too, so the
    rest of the run is not held hostage to a bug already reported.

    Only the client database (``_c``): it is truncate-isolated, so any open
    transaction there once the test is over is a leak by definition. The
    integration database uses a session-long connection with per-test rollback,
    where a transaction between tests can be the harness itself. ``client``,
    ``python_client`` and ``db_factory`` all sit on this same database (all
    three build their engine on ``TEST_DATABASE_URL``), so all three name it
    here — ``db_factory``'s own teardown check (see the fixture) is meant to
    catch a leak first, by cancelling the task that holds it; this is the
    backstop for whatever gets past that.

    Cost: one connection to the maintenance DB per client-DB test, a few ms.
    """
    yield
    names = getattr(item, "fixturenames", ())
    if not ({"client", "python_client", "db_factory"} & set(names)):
        return
    leaked = asyncio.run(_terminate_open_transactions(_CLIENT_DB_NAME))
    if leaked:
        details = "\n".join(
            f"  pid={r['pid']} open for {r['xact_age']}\n"
            f"    last statement: {r['query']}"
            for r in leaked
        )
        pytest.fail(
            f"{item.nodeid} left {len(leaked)} transaction(s) open on"
            f" {_CLIENT_DB_NAME} after its fixtures tore down (terminated now)."
            " Whatever opened them never committed, rolled back, or closed —"
            " usually a session held by a task that outlived the test's event"
            " loop:\n" + details,
            pytrace=False,
        )


@pytest.fixture(autouse=True)
def _pg_schema_gate(request: pytest.FixtureRequest) -> None:
    """Provision the DB schema for the tests that need it — and only those.

    ``_pg_schema`` used to be ``autouse=True`` at session scope, which meant a
    host with no Postgres could not run *any* test, including the great majority
    of unit files that never touch a database: the session fixture errored during
    setup and took the whole run down with it. Gating it per-test keeps behaviour
    identical for DB-backed tests (still built once per session — ``_pg_schema``
    is still session-scoped) while letting every unit test that asks for nothing
    run with no server at all. A unit test that DOES name a DB fixture (the turn
    log lives in Postgres, so the runner's tests do) pays for one; the rest
    still do not.
    """
    if _needs_db(request):
        request.getfixturevalue("_pg_schema")


@pytest.fixture(scope="session")
def _pg_schema():
    """Create + migrate THIS worker's two dedicated databases once per session:
    the integration DB (settings.database_url, bound by the app engines) and the
    client/python_client DB (TEST_DATABASE_URL). Reached via the ``_pg_schema_gate``
    autouse fixture above so the integration harness — which binds to
    settings.database_url — always finds a ready schema too. Both are per-worker,
    so nothing races across xdist workers. Talks to the Postgres server at
    TEST_PG_BASE (local docker pg :5433 by default; CI overrides it).
    """
    global _TEMPLATE_READY
    _TEMPLATE_READY = _ensure_template()
    _create_and_migrate(_INTG_DB_NAME, settings.database_url)
    _create_and_migrate(_CLIENT_DB_NAME, TEST_DATABASE_URL)
    yield


def _is_anyio_runner_plumbing(task: asyncio.Task) -> bool:
    """Whether ``task`` is anyio's own pytest-runner machinery, not test work.

    Every async fixture step (this teardown included) is driven through
    ``anyio.pytest_plugin``'s ``TestRunner._call_in_runner_task``: the caller
    wraps itself in a task via ``run_until_complete`` and suspends on
    ``await future``, and that future only resolves once THIS very coroutine
    returns — so that caller task is always still "pending" by construction
    at the exact moment this check runs, for every fixture and test, leak or
    not. It is identified by where its code lives (anyio's own package),
    not by name, since it is the same bound method regardless of which
    fixture or test it is currently ferrying.
    """
    code = getattr(task.get_coro(), "cr_code", None)
    filename = getattr(code, "co_filename", "") or ""
    return f"{os.sep}anyio{os.sep}" in filename


async def _fail_on_background_work(label: str) -> None:
    """Refuse to let a test return while something it started is still running.

    A test that submits work onto a runner (``AgentWorkRunner.submit``) and
    returns without waiting for it races the per-test event loop's own
    teardown: whatever task is still going gets frozen mid-await the moment
    the loop closes under it — mid a DB transaction, most dangerously, holding
    a lock the next test's ``TRUNCATE`` then waits on (see
    ``pytest_runtest_teardown`` above, which is the backstop for whatever gets
    past this).

    So: wait briefly (a turn's tail is milliseconds), and if anything is still
    pending, cancel it — cancelling on the still-live loop is what makes the
    leak impossible, since an ``async with session_factory()`` that gets
    cancelled rolls back and closes right here rather than freezing — then
    fail loudly naming every offending coroutine, instead of letting the next
    test silently inherit the lock.
    """
    current = asyncio.current_task()
    pending = {
        t
        for t in asyncio.all_tasks()
        if t is not current and not t.done() and not _is_anyio_runner_plumbing(t)
    }
    if not pending:
        return
    _, still_pending = await asyncio.wait(pending, timeout=2.0)
    if not still_pending:
        return
    offenders = sorted(t.get_coro().__qualname__ for t in still_pending)
    for t in still_pending:
        t.cancel()
    # Give the cancellation itself a moment to actually unwind (rollback +
    # close) before the caller's next teardown step (disposing the engine
    # these tasks' sessions borrow connections from).
    await asyncio.wait(still_pending, timeout=2.0)
    pytest.fail(
        f"{label} returned with background work still running: "
        + ", ".join(offenders)
        + ". A test must not return while a runner's turn task is still"
        " going — await `runner.drain()` before returning.",
        pytrace=False,
    )


@pytest.fixture
async def db_factory(_pg_schema):
    """A truncated database and a session factory over it — no app around it.

    ``client`` builds a whole TestClient to arrive at one of these. A test that
    only seeds and reads rows should not pay for an ASGI app to do it, and
    saying so in the fixture list is also how ``_pg_schema_gate`` learns this
    test needs a database at all.
    """
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    await _truncate_all(engine)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await _fail_on_background_work("db_factory")
    await engine.dispose()


@pytest.fixture
async def python_client(
    _pg_schema,
    stub_hooks: StubChannel,
    tmp_path,
):
    """Async httpx client bound to the app over ASGI — the async counterpart to
    `client`. Inherited contract/route tests written against the main backend use
    it. Same postgres test DB + truncate isolation + agent seed as `client`, but
    awaitable inside anyio tests."""
    from httpx import ASGITransport, AsyncClient

    from app.domain.identity.services import IdentityService

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    test_factory = async_sessionmaker(engine, expire_on_commit=False)
    await _truncate_all(engine)
    get_broker().reset()  # channel ids reset with the DB; drop stale buffered frames
    async with test_factory() as session:
        await IdentityService(session).ensure_agent_user()
        await session.commit()

    async def override_get_db():
        async with test_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    chat_service = ChatService(
        session_factory=test_factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([stub_hooks.runtime], stub_hooks.name),
    )

    def override_get_chat_service() -> ChatService:
        # ONE instance, like production's lru_cache. A per-request instance was
        # harmless while a turn was self-contained; it is not now that the reply
        # arrives on a subscription owned by the service that started the turn.
        return chat_service

    # ONE get_db across the whole app (app.db.session re-exports app.core.db's),
    # so a single override moves every route — cheesex and 知是 alike — onto the
    # per-worker test factory (NullPool, isolated DB, loop-safe).
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_chat_service] = override_get_chat_service

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Cheese-Token": SANDBOX_TOKEN},
    ) as c:
        # Expose the per-worker factory so contract tests can seed rows (e.g. a
        # real authenticated user) on the SAME DB the app reads through get_db.
        c.test_factory = test_factory  # type: ignore[attr-defined]
        yield c

    app.dependency_overrides.clear()
    await _fail_on_background_work("python_client")
    await engine.dispose()


# --- shared seed helpers -----------------------------------------------------


def seed_space(client: TestClient, name: str = "信院") -> int:
    """Insert a 知是 Space row directly and return its int id.

    The cheesex ``POST /api/spaces`` uuid stub was retired in the fusion merge
    (unify P1b/c: one Space = main int). The task-template market still lives on
    top of a Space, so tests that need one seed it through the DB here.
    """
    import asyncio as _asyncio
    from datetime import UTC, datetime

    from app.domain.space.models import Space

    holder: dict[str, int] = {}

    async def _seed() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            now = datetime.now(UTC)
            space = Space(
                name=name,
                intro="",
                description="",
                announcements=[],
                task_templates=[],
                created_at=now,
                updated_at=now,
            )
            session.add(space)
            await session.flush()
            holder["id"] = space.id
            await session.commit()

    _asyncio.run(_seed())
    return holder["id"]


def seed_task_with_protocol(
    client: TestClient,
    *,
    conditions: list[dict] | None = None,
    resource_pack: dict | None = None,
    default_role: str | None = None,
    override: dict | None = None,
    space_id: int | None = None,
) -> int:
    """A 项目集 carrying 机构协议 + one 赛题 under it; returns the 赛题's int id.

    Seeded through the DB because the 知是 publish flow needs an authenticated
    space admin and a filled form, and none of that is what the protocol tests
    are about. `override` populates the 赛题's own `protocol_override` (#370
    option (c)).
    """
    import asyncio as _asyncio
    from datetime import UTC, datetime

    from app.domain.space.models import Space, SpaceCategory
    from app.domain.task.models import Task

    holder: dict[str, int] = {}

    async def _seed() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            now = datetime.now(UTC)
            owning_space = space_id
            if owning_space is None:
                space = Space(
                    name=f"信院-{datetime.now(UTC).timestamp()}",
                    intro="",
                    description="",
                    announcements=[],
                    task_templates=[],
                    created_at=now,
                    updated_at=now,
                )
                session.add(space)
                await session.flush()
                owning_space = space.id
            category = SpaceCategory(
                space_id=owning_space,
                name="创研课 2026 秋",
                description="",
                display_order=0,
                resource_pack=resource_pack or {},
                conditions=conditions or [],
                default_role=default_role,
                created_at=now,
                updated_at=now,
            )
            session.add(category)
            await session.flush()
            task = Task(
                name="题目",
                intro="",
                description="",
                protocol_override=override,
                creator_id=1,
                space_id=owning_space,
                category_id=category.id,
                submitter_type=0,
                approved=0,
                default_deadline=0,
                created_at=now,
                updated_at=now,
            )
            session.add(task)
            await session.flush()
            holder["id"] = task.id
            await session.commit()

    _asyncio.run(_seed())
    return holder["id"]


def seed_user(client: TestClient, handle: str) -> str:
    """Get-or-create a real 知是 User for ``handle`` and return a session token
    whose ``sub`` is the int user id (so ActorResolver resolves ``user_id``).

    The cheesex POST /api/users/login handle-login was retired in the fusion
    merge (unify P3). Flows that need a genuine logged-in human with a DB-backed
    user id (e.g. device approval binding an owner) use this instead of a bare
    handle token.
    """
    import asyncio as _asyncio

    from app.common.auth import create_access_token
    from app.domain.user.repositories import UserRepository

    holder: dict[str, int] = {}

    async def _seed() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            repo = UserRepository(session)
            user = await repo.get_by_username(handle)
            if user is None:
                user = await repo.create_user(
                    username=handle, email=f"{handle}@example.com"
                )
            holder["id"] = user.id
            await session.commit()

    _asyncio.run(_seed())
    return create_access_token(holder["id"], handle=handle)
