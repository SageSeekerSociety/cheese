"""The model route remote machines use (/api/llm).

A MicroCloud machine gets its own scoped cheese token, never a provider key —
so this route must authenticate that token, swap in the project's virtual
gateway key, and pass the upstream answer through untouched.
"""

import uuid

import httpx
import pytest

from app.api.routes import llm_proxy
from app.core.sandbox_auth import mint_scoped_token


def _make_project(client) -> str:
    r = client.post("/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


class _FakeResponse:
    def __init__(self, status_code: int, chunks: list[bytes], headers: dict):
        self.status_code = status_code
        self.headers = headers
        self._chunks = chunks

    async def aiter_raw(self):
        for c in self._chunks:
            yield c

    async def aclose(self) -> None:
        return None


class _FakeClient:
    """Captures the outbound request instead of talking to a real gateway."""

    seen: dict = {}

    def __init__(self, *a, **kw):
        pass

    def build_request(self, method, url, *, headers, content, params):
        _FakeClient.seen = {
            "method": method,
            "url": url,
            "headers": headers,
            "content": content,
            "params": params,
        }
        return _FakeClient.seen

    async def send(self, request, stream: bool = False):
        return _FakeResponse(
            200,
            [b'{"ok":', b"true}"],
            {"content-type": "application/json", "content-length": "999"},
        )

    async def aclose(self) -> None:
        return None


@pytest.fixture
def _pool(monkeypatch):
    monkeypatch.setattr(llm_proxy.settings, "anthropic_base_url", "http://pool:4000")
    monkeypatch.setattr(llm_proxy.httpx, "AsyncClient", _FakeClient)


@pytest.fixture
def _project_key(monkeypatch):
    async def fake_key(self, project_id):  # noqa: ANN001
        return "sk-virtual-for-" + str(project_id)[:8]

    monkeypatch.setattr(
        "app.domain.agent.chat.ChatService.project_gateway_key", fake_key
    )


def test_scoped_token_is_swapped_for_the_project_key(client, _pool, _project_key):
    pid = _make_project(client)
    token = mint_scoped_token(project_id=pid, topic_id=str(uuid.uuid4()))

    r = client.post(
        "/llm/v1/messages",
        headers={"Authorization": f"Bearer {token}", "anthropic-version": "2023-06-01"},
        content=b'{"model":"m","messages":[]}',
    )

    assert r.status_code == 200
    assert r.content == b'{"ok":true}'
    sent = _FakeClient.seen
    assert sent["url"] == "http://pool:4000/v1/messages"
    assert sent["headers"]["authorization"] == f"Bearer sk-virtual-for-{pid[:8]}"
    assert sent["headers"]["x-api-key"] == f"sk-virtual-for-{pid[:8]}"
    # The machine's own token must not ride along past the swap.
    assert token not in str(sent["headers"])
    # Protocol headers the client set are preserved.
    assert sent["headers"]["anthropic-version"] == "2023-06-01"
    assert sent["content"] == b'{"model":"m","messages":[]}'


def test_x_api_key_presentation_also_works(client, _pool, _project_key):
    pid = _make_project(client)
    token = mint_scoped_token(project_id=pid)

    r = client.post("/llm/v1/messages", headers={"x-api-key": token}, content=b"{}")

    assert r.status_code == 200


def test_missing_or_bad_token_is_rejected(client, _pool, _project_key):
    assert client.post("/llm/v1/messages", content=b"{}").status_code == 401
    r = client.post(
        "/llm/v1/messages",
        headers={"Authorization": "Bearer not-a-real-token"},
        content=b"{}",
    )
    assert r.status_code == 401


def test_no_pool_configured_is_refused(client, monkeypatch, _project_key):
    monkeypatch.setattr(llm_proxy.settings, "anthropic_base_url", None)
    pid = _make_project(client)
    token = mint_scoped_token(project_id=pid)

    r = client.post(
        "/llm/v1/messages",
        headers={"Authorization": f"Bearer {token}"},
        content=b"{}",
    )
    assert r.status_code >= 400


def test_no_project_key_refuses_rather_than_using_pool_credentials(
    client, _pool, monkeypatch
):
    async def no_key(self, project_id):  # noqa: ANN001
        return None

    monkeypatch.setattr("app.domain.agent.chat.ChatService.project_gateway_key", no_key)
    pid = _make_project(client)
    token = mint_scoped_token(project_id=pid)

    r = client.post(
        "/llm/v1/messages",
        headers={"Authorization": f"Bearer {token}"},
        content=b"{}",
    )
    assert r.status_code >= 400


def test_upstream_failure_surfaces_as_gateway_error(client, monkeypatch, _project_key):
    monkeypatch.setattr(llm_proxy.settings, "anthropic_base_url", "http://pool:4000")

    class _Boom(_FakeClient):
        async def send(self, request, stream: bool = False):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(llm_proxy.httpx, "AsyncClient", _Boom)
    pid = _make_project(client)
    token = mint_scoped_token(project_id=pid)

    r = client.post(
        "/llm/v1/messages",
        headers={"Authorization": f"Bearer {token}"},
        content=b"{}",
    )
    assert r.status_code >= 400


@pytest.mark.anyio
async def test_admission_requires_a_scoped_token(client):
    r = client.post("/llm/admission")
    assert r.status_code == 401


@pytest.mark.anyio
async def test_admission_answers_from_the_grant_balance(client):
    """One budget, two enforcement points: the same compute grants the gateway
    prices into max_budget answer the subscription proxy's yes/no here."""
    pid = _make_project(client)
    token = mint_scoped_token(project_id=pid)

    # No grants at all = 自治项目: never refused.
    r = client.post("/llm/admission", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["allow"] is True and body["reason"] == "unlimited"

    from app.domain.usage.repositories import ComputeGrantRepository

    async with client.test_factory() as session:
        await ComputeGrantRepository(session).grant(
            project_id=uuid.UUID(pid), source_task_id=None, credits_total=5.0
        )
        await session.commit()

    r = client.post("/llm/admission", headers={"Authorization": f"Bearer {token}"})
    assert r.json()["data"]["allow"] is True

    async with client.test_factory() as session:
        await ComputeGrantRepository(session).consume(uuid.UUID(pid), 5.0)
        await session.commit()

    r = client.post("/llm/admission", headers={"Authorization": f"Bearer {token}"})
    body = r.json()["data"]
    assert body["allow"] is False
    assert "5.0000" in body["reason"]


async def _exhaust(client, pid: str) -> None:
    from app.domain.usage.repositories import ComputeGrantRepository

    async with client.test_factory() as session:
        await ComputeGrantRepository(session).grant(
            project_id=uuid.UUID(pid), source_task_id=None, credits_total=1.0
        )
        await ComputeGrantRepository(session).consume(uuid.UUID(pid), 1.0)
        await session.commit()


@pytest.mark.anyio
async def test_admission_tells_the_room_once_for_a_refused_turn_in_flight(client):
    """The metering proxy caches a verdict for 30s and Claude Code retries ten
    times, so admission gets asked again and again for the SAME refusal
    (#715) — asking five times must still post the room's exhaustion notice
    exactly once, on the turn admission actually refused."""
    from app.domain.block.repositories import BlockRepository
    from app.domain.usage.credits import CREDITS_EXHAUSTED_EVENT
    from tests.turn_log import open_turn

    pid = _make_project(client)
    topic_id = client.post("/topics", json={"project_id": pid, "title": "T"}).json()[
        "data"
    ]["id"]
    turn_id = await open_turn(client.test_factory, uuid.UUID(topic_id))
    await _exhaust(client, pid)
    token = mint_scoped_token(project_id=pid, topic_id=topic_id)
    headers = {"Authorization": f"Bearer {token}"}

    for _ in range(5):
        r = client.post("/llm/admission", headers=headers)
        assert r.json()["data"]["allow"] is False

    async with client.test_factory() as session:
        blocks = await BlockRepository(session).list_for_topic(uuid.UUID(topic_id))
    notices = [b for b in blocks if b.content == CREDITS_EXHAUSTED_EVENT]
    assert len(notices) == 1
    assert notices[0].turn_id == turn_id


@pytest.mark.anyio
async def test_admission_refusal_posts_nothing_with_no_turn_running(client):
    """No turn is in flight at this place — that is the turn-START refusal
    path's job (it already posts its own exhaustion notice), not admission's.
    A refusal here must invent nothing."""
    from app.domain.block.repositories import BlockRepository
    from app.domain.usage.credits import CREDITS_EXHAUSTED_EVENT

    pid = _make_project(client)
    topic_id = client.post("/topics", json={"project_id": pid, "title": "T"}).json()[
        "data"
    ]["id"]
    await _exhaust(client, pid)
    token = mint_scoped_token(project_id=pid, topic_id=topic_id)
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post("/llm/admission", headers=headers)
    assert r.json()["data"]["allow"] is False

    async with client.test_factory() as session:
        blocks = await BlockRepository(session).list_for_topic(uuid.UUID(topic_id))
    assert not any(b.content == CREDITS_EXHAUSTED_EVENT for b in blocks)


@pytest.mark.anyio
async def test_admission_says_which_pool_serves_the_project(client, monkeypatch):
    """The proxy asks once and learns both things: may it run, and where does
    it go (#243). Where it goes is a project setting, so supply can change
    without touching the proxy or restarting the sandbox."""
    from app.core.config import settings as app_settings
    from app.domain.project.repositories import ProjectRepository

    monkeypatch.setattr(app_settings, "subscription_enabled", True)
    pid = _make_project(client)
    token = mint_scoped_token(project_id=pid)
    headers = {"Authorization": f"Bearer {token}"}

    # Default: the deployment's own supply, and no key travels for it — the
    # subscription credential lives on the proxy, never in a control-plane body.
    body = client.post("/llm/admission", headers=headers).json()["data"]
    assert body["supply"] == {"pool": "subscription"}
    assert body["allow"] is True

    async with client.test_factory() as session:
        project = await ProjectRepository(session).get(uuid.UUID(pid))
        assert project is not None
        project.settings = {"supply": "gateway"}
        await session.commit()

    body = client.post("/llm/admission", headers=headers).json()["data"]
    assert body["supply"]["pool"] == "gateway"
    # No gateway configured in this harness → no key. The proxy refuses on an
    # empty key rather than serving the project from a pool it did not choose.
    assert body["supply"].get("key") is None


# --- which ccproxy identity a turn goes out as ------------------------------
# ccproxy only honours a machine's ticket over that machine's OWN identity
# (measured 2026-08-14: m516's ticket over an m161 connection is a 401 with no
# request_id). So the proxy must learn WHICH identity before it forwards, and
# admission is the hop it already waits on.


async def _pin_topic_to_machine(
    client, *, project_id: str, topic_id, machine_id: int, upstream: str | None
):
    """A topic pinned to an enrolled machine — the real chain admission walks:
    topic → pinned device → machine row."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.domain.device.models import DeviceRow, DeviceTopicRow
    from app.domain.machine.models import ProjectMachine
    from app.domain.user.models import User

    device_id = f"dev-{machine_id}"
    async with client.test_factory() as session:
        owner = (await session.execute(select(User).limit(1))).scalar_one()
        session.add(
            DeviceRow(
                device_id=device_id,
                name=f"machine-{machine_id}",
                token=f"tok-{device_id}",
                owner_user_id=owner.id,
                created_at=datetime.now(UTC),
            )
        )
        await session.flush()
        session.add(
            ProjectMachine(
                project_id=uuid.UUID(project_id),
                machine_id=machine_id,
                customer_id=1,
                account_id=1,
                offering_id=1,
                hostname=f"host-{machine_id}",
                login_user="cheese",
                cores=2,
                memory_mb=4096,
                disk_gb=20,
                device_id=device_id,
                enrolled_at=datetime.now(UTC),
                ccproxy_upstream=upstream,
            )
        )
        session.add(DeviceTopicRow(topic_id=topic_id, device_id=device_id))
        await session.commit()


async def test_admission_names_the_machine_identity_a_topics_turns_go_out_as(
    client, monkeypatch
):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "subscription_enabled", True)
    pid = _make_project(client)
    topic_id = uuid.uuid4()
    headers = {
        "Authorization": "Bearer "
        + mint_scoped_token(project_id=pid, topic_id=str(topic_id))
    }

    # Nothing pinned yet: there is no machine, so there is no identity to name.
    body = client.post("/llm/admission", headers=headers).json()["data"]
    assert body["supply"] == {"pool": "subscription"}

    await _pin_topic_to_machine(
        client, project_id=pid, topic_id=topic_id, machine_id=516, upstream="m516:pw516"
    )

    body = client.post("/llm/admission", headers=headers).json()["data"]
    assert body["supply"]["upstream"] == "m516:pw516"


async def test_a_machine_without_a_recorded_identity_names_none(client, monkeypatch):
    """Enrollment is the only moment the platform is on the machine over ssh
    (the bootstrap key is erased the instant it succeeds), so machines enrolled
    before this existed keep NULL forever. NULL must read as "use the
    deployment-wide identity" — never as an empty string the proxy would then
    try to authenticate with."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "subscription_enabled", True)
    pid = _make_project(client)
    topic_id = uuid.uuid4()
    await _pin_topic_to_machine(
        client, project_id=pid, topic_id=topic_id, machine_id=161, upstream=None
    )

    body = client.post(
        "/llm/admission",
        headers={
            "Authorization": "Bearer "
            + mint_scoped_token(project_id=pid, topic_id=str(topic_id))
        },
    ).json()["data"]
    assert "upstream" not in body["supply"]


async def _pin_topic_to_self_hosted_device(
    client, *, topic_id, device_id: str, upstream: str | None
):
    """A topic pinned to a SELF-HOSTED device — no machine row at all. This is
    the dev box's shape: it was never enrolled from MicroCloud, so its ccproxy
    identity lives on the device row itself."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.domain.device.models import DeviceRow, DeviceTopicRow
    from app.domain.user.models import User

    async with client.test_factory() as session:
        owner = (await session.execute(select(User).limit(1))).scalar_one()
        session.add(
            DeviceRow(
                device_id=device_id,
                name=device_id,
                token=f"tok-{device_id}",
                owner_user_id=owner.id,
                created_at=datetime.now(UTC),
                ccproxy_upstream=upstream,
            )
        )
        session.add(DeviceTopicRow(topic_id=topic_id, device_id=device_id))
        await session.commit()


async def test_admission_names_a_self_hosted_devices_own_identity(client, monkeypatch):
    """The self-hosted twin of the machine case: the dev box brings its own
    ccproxy identity on the DEVICE row (it has no enrollment and no machine
    row), and admission must surface it the same way — one credential model for
    every compute form, or the box stays chained to the platform-credential
    swap path that #393 is about."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "subscription_enabled", True)
    pid = _make_project(client)
    topic_id = uuid.uuid4()
    await _pin_topic_to_self_hosted_device(
        client, topic_id=topic_id, device_id="the-box", upstream="m161:pw161"
    )

    headers = {
        "Authorization": "Bearer "
        + mint_scoped_token(project_id=pid, topic_id=str(topic_id))
    }
    body = client.post("/llm/admission", headers=headers).json()["data"]

    assert body["supply"]["upstream"] == "m161:pw161"


async def test_a_device_with_no_identity_still_names_none(client, monkeypatch):
    """Every laptop-class self-hosted device: NULL means "platform pool", never
    an empty identity the proxy would try to authenticate with."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "subscription_enabled", True)
    pid = _make_project(client)
    topic_id = uuid.uuid4()
    await _pin_topic_to_self_hosted_device(
        client, topic_id=topic_id, device_id="a-laptop", upstream=None
    )

    headers = {
        "Authorization": "Bearer "
        + mint_scoped_token(project_id=pid, topic_id=str(topic_id))
    }
    body = client.post("/llm/admission", headers=headers).json()["data"]

    assert "upstream" not in body["supply"]


async def _room_with_a_thread(client, project_id: str) -> tuple[str, str]:
    """A real room and one thread of work in it — (room_id, thread_id).

    Both halves have to be real rows, not two uuids: the whole failure is that
    a thread's id is not a `topics` id, so a test that invents one would place
    the pin and the token on the same key and pass either way.
    """
    from app.domain.room_task.services import TaskService

    room_id = client.post(
        "/topics",
        json={"project_id": project_id, "title": "房间", "created_by": "alice"},
    ).json()["data"]["id"]
    async with client.test_factory() as session:
        task = await TaskService(session).open_thread(
            project_id=uuid.UUID(project_id),
            room_id=uuid.UUID(room_id),
            title="一件活",
            owner_handle="alice",
            created_by="alice",
        )
        thread_id = str(task.id)
        await session.commit()
    return room_id, thread_id


async def test_admission_places_a_threads_turn_on_its_rooms_machine(
    client, monkeypatch
):
    """A thread's per-turn token carries the THREAD's id; the machine pin is the
    ROOM's (`bind_topic_device` binds nothing else, and a thread runs on its
    room's machine — #702). So this lookup came back empty for every thread on
    an enrolled machine.

    Empty is not the harmless "use the deployment-wide identity" it is for a
    room. A caller carrying its own ccproxy ticket is REFUSED when no identity
    is resolved, rather than billed to the platform — so every thread turn on an
    enrolled machine was refused, and the refusal crashed the proxy on its way
    out, which is what reached the user as a request timeout.
    """
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "subscription_enabled", True)
    pid = _make_project(client)
    room_id, thread_id = await _room_with_a_thread(client, pid)
    await _pin_topic_to_machine(
        client,
        project_id=pid,
        topic_id=uuid.UUID(room_id),
        machine_id=784,
        upstream="m784:pw784",
    )

    def _supply(place_id: str) -> dict:
        headers = {
            "Authorization": "Bearer "
            + mint_scoped_token(project_id=pid, topic_id=place_id)
        }
        return client.post("/llm/admission", headers=headers).json()["data"]["supply"]

    assert _supply(thread_id)["upstream"] == "m784:pw784"
    # The room's own turns must be unaffected — the thread is resolved THROUGH
    # the room, not instead of it.
    assert _supply(room_id)["upstream"] == "m784:pw784"


async def test_admission_still_names_nothing_for_a_place_that_owns_no_machine(
    client, monkeypatch
):
    """Resolving a thread to its room must not invent an identity. A room with
    no pin, and a thread in it, both still mean "use the deployment-wide one" —
    the behaviour every unplaced turn has today."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "subscription_enabled", True)
    pid = _make_project(client)
    room_id, thread_id = await _room_with_a_thread(client, pid)

    for place_id in (room_id, thread_id):
        headers = {
            "Authorization": "Bearer "
            + mint_scoped_token(project_id=pid, topic_id=place_id)
        }
        body = client.post("/llm/admission", headers=headers).json()["data"]
        assert "upstream" not in body["supply"]


async def test_a_thread_with_its_own_pin_is_not_answered_from_its_room(
    client, monkeypatch
):
    """The other direction, and the reason the room is a FALLBACK and not the
    answer. On a self-hosted device the resolver pins whatever place it is given,
    so a thread pins itself — and two threads of one room can land on two
    different boxes. Resolving a thread through its room would then name a
    machine its turns do not run on, which is the same wrong-identity failure
    read backwards: ccproxy only honours a ticket over its own machine's
    connection."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "subscription_enabled", True)
    pid = _make_project(client)
    room_id, thread_id = await _room_with_a_thread(client, pid)
    await _pin_topic_to_machine(
        client,
        project_id=pid,
        topic_id=uuid.UUID(room_id),
        machine_id=800,
        upstream="m800:pw800",
    )
    await _pin_topic_to_self_hosted_device(
        client,
        topic_id=uuid.UUID(thread_id),
        device_id="the-threads-own-box",
        upstream="m801:pw801",
    )

    headers = {
        "Authorization": "Bearer "
        + mint_scoped_token(project_id=pid, topic_id=thread_id)
    }
    body = client.post("/llm/admission", headers=headers).json()["data"]

    assert body["supply"]["upstream"] == "m801:pw801"
