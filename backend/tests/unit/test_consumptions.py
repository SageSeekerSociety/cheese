"""A question is read to its end by whichever backend is up when it ends.

Two backends share the session host, the journal mirrors and Valkey, as a
rollout's old and new processes do. The session is the pinned pi, answering
from a fake model that holds the rest of its answer back until told.

Rules held here:

* a backend leaving mid-answer (a deploy) hands the question over at once, and
  the other one finishes it: one end, the whole answer, nothing said twice;
* a backend that dies mid-answer hands it over once its lease lapses;
* two backends looking at once take a question up once;
* a question taken over is still stopped by its asker;
* the slot a question holds is still held after the takeover, and let go at
  its end;
* a viewer reconnecting from where it was gets the rest, and only the rest;
* a question whose runner the machine says is gone ends at once, instead of
  being taken up again on every sweep.
"""

import asyncio
import threading
import time
import uuid

import pytest
from redis.asyncio import from_url

from app.core.config import settings
from app.domain.agent import admission
from app.domain.agent.device_hub import DeviceCallError
from app.domain.agent.session_host import consumptions as consumptions_module
from app.domain.agent.session_host.answer import Words
from app.domain.agent.session_host.consumptions import Consumptions
from app.domain.agent.session_host.contract import Prompt
from app.domain.agent.session_host.host import SessionHost
from tests.support.session_host import DEVICE, Host, install_pi, stop_all
from tests.unit.test_personal_sessions import Platform, Started, question_credential

ANSWER = "第一句话写在这里。第二句话写在后面。第三句收尾。"


class Recorder:
    """A kind of question that keeps what it was told."""

    def __init__(self, reader: Consumptions | None = None) -> None:
        self.reader = reader
        self.steps: list = []
        self.ends: list[dict] = []
        self.stop = False

    async def stopped(self, consumption) -> bool:
        return self.stop

    async def took(self, consumption, item) -> None:
        self.steps.append(item)
        if self.reader is not None and isinstance(item, Words):
            await self.reader.publish(
                consumption, "words", {"text": item.text, "at": item.at}
            )

    async def ended(self, consumption, answer, *, written, stopped, failure):
        self.ends.append(
            {
                "answer": answer,
                "written": written,
                "stopped": stopped,
                "failure": failure,
            }
        )
        return [("done", {"answer": answer.text, "stopped": stopped})]


@pytest.fixture
def hub(tmp_path, monkeypatch):
    home = tmp_path / "host"
    install_pi(home)
    monkeypatch.setattr(settings, "agent_session_device_id", DEVICE)
    yield Host(home)
    stop_all(home)


@pytest.fixture
def platform(monkeypatch):
    made: list[Platform] = []

    def make(steps, **options) -> Platform:
        fake = Platform(steps, **options)
        monkeypatch.setattr(settings, "agent_session_api_base", fake.url)
        made.append(fake)
        return fake

    yield make
    for fake in made:
        fake.close()


@pytest.fixture
def valkey():
    client = from_url(settings.redis_url)
    return lambda: client


@pytest.fixture
def backends(hub, tmp_path, valkey):
    """Two backends on one session host; each serves the same kind of
    question with a consumer of its own."""
    kind = f"test-{uuid.uuid4().hex[:8]}"
    made = []
    for name in ("old", "new"):
        reader = Consumptions(
            SessionHost(hub, mirrors=tmp_path / "mirrors"), valkey, me=name
        )
        recorder = Recorder(reader)
        reader.serve(kind, recorder)
        made.append((reader, recorder))
    return kind, made


async def _until(check, timeout: float = 60.0) -> None:
    async with asyncio.timeout(timeout):
        while not await check():
            await asyncio.sleep(0.1)


async def _first_words(recorder: "Recorder", model: Platform) -> None:
    """Wait for the answer's first words to reach ``recorder``; failing, say
    what did reach it, so a timeout names the step that never came."""

    async def started() -> bool:
        return _words(recorder) > 0

    began = time.monotonic()
    try:
        await _until(started)
    except TimeoutError:
        written = [(round(at - began, 2), text) for at, text in model.written]
        raise AssertionError(
            f"no words in 60 s: the reader took {recorder.steps!r}, "
            f"ended {recorder.ends!r}; the model was asked {len(model.requests)} "
            f"time(s) and wrote (seconds into the wait, carrying text?) {written}"
        ) from None


async def _begin(reader: Consumptions, kind: str, *, slots=()) -> str:
    launch = Started(7)
    work = uuid.uuid4()
    await reader.begin(
        kind=kind,
        key=str(uuid.uuid4()),
        data={},
        work_id=work,
        session=tuple(launch),  # type: ignore[arg-type]
        prompt=Prompt(work, "说三句话。", acting=question_credential(7)),
        ceiling_s=120,
        slots=list(slots),
    )
    return str(work)


async def _state(reader: Consumptions, work: str) -> str:
    """What a sweep would find of ``work``, read before it looks."""
    redis = reader._valkey()
    lease = await redis.get(consumptions_module._lease(work))
    ttl = await redis.pttl(consumptions_module._lease(work))
    record = await reader.get(work)
    return (
        f"open={await redis.sismember(consumptions_module._OPEN, work)} "
        f"lease={lease!r} ttl_ms={ttl} reading={work in reader._reading} "
        f"ended={record.ended if record else None}"
    )


def _words(recorder: Recorder) -> int:
    return len([step for step in recorder.steps if isinstance(step, Words)])


async def _ended(recorder: Recorder) -> bool:
    return bool(recorder.ends)


@pytest.mark.anyio
async def test_a_backend_leaving_mid_answer_hands_it_over_at_once(backends, platform):
    kind, ((old, before), (new, after)) = backends
    release = threading.Event()
    model = platform([{"text": ANSWER}], rest_held_until=release)
    work = await _begin(old, kind)

    await _first_words(before, model)
    await old.let_go()
    assert await new.sweep() == 1
    release.set()
    await _until(lambda: _ended(after))

    assert before.ends == []
    assert len(after.ends) == 1
    end = after.ends[0]
    assert end["answer"].text == ANSWER and end["failure"] is None
    # What viewers were told adds up to the answer, once, whoever told it.
    told = ""
    async for _position, event, data in new.watch(work):
        if event == "words":
            told = told[: data["at"]] + data["text"]
    assert told == ANSWER
    before_sweep = await _state(new, work)
    assert await new.sweep() == 0, before_sweep


class _OpenSetReadEarlier:
    """A Valkey client whose sweep finds the open questions as they were when
    ``work`` was still among them: the set is read once, and each question in
    it is looked at in turn, so one can end between the two."""

    def __init__(self, client, work: str) -> None:
        self._client = client
        self._work = work

    async def smembers(self, name):
        return {self._work.encode()}

    def __getattr__(self, name):
        return getattr(self._client, name)


@pytest.mark.anyio
async def test_a_question_that_ends_while_a_sweep_looks_is_not_taken_up_again(
    backends, platform, valkey
):
    kind, ((old, before), (new, _after)) = backends
    platform([{"text": ANSWER}])
    work = await _begin(old, kind)
    await _until(lambda: _ended(before))
    redis = valkey()

    async def gone() -> bool:
        return not await redis.sismember(
            consumptions_module._OPEN, work
        ) and not await redis.exists(consumptions_module._lease(work))

    await _until(gone)
    late = Consumptions(new._host, lambda: _OpenSetReadEarlier(redis, work), me="late")
    late.serve(kind, Recorder())

    assert await late.sweep() == 0
    assert not await redis.exists(consumptions_module._lease(work))


@pytest.mark.anyio
async def test_a_backend_dying_mid_answer_hands_it_over_when_its_lease_lapses(
    backends, platform, monkeypatch
):
    monkeypatch.setattr(consumptions_module, "LEASE_S", 1.0)
    monkeypatch.setattr(consumptions_module, "RENEW_S", 0.2)
    kind, ((old, before), (new, after)) = backends
    release = threading.Event()
    model = platform([{"text": ANSWER}], rest_held_until=release)
    await _begin(old, kind)

    await _first_words(before, model)
    assert await new.sweep() == 0
    # Dies: stops reading and renewing, gives nothing up.
    for task in list(old._reading.values()):
        task.cancel()
    assert await new.sweep() == 0

    async def taken() -> bool:
        return await new.sweep() == 1

    await _until(taken, timeout=10)
    release.set()
    await _until(lambda: _ended(after))

    assert before.ends == []
    assert [end["answer"].text for end in after.ends] == [ANSWER]


@pytest.mark.anyio
async def test_two_backends_looking_at_once_take_a_question_up_once(backends, platform):
    kind, ((old, before), (new, after)) = backends
    release = threading.Event()
    model = platform([{"text": ANSWER}], rest_held_until=release)
    await _begin(old, kind)

    await _first_words(before, model)
    await old.let_go()
    third = Consumptions(new._host, new._redis, me="third")
    third.serve(kind, Recorder())
    taken = await asyncio.gather(new.sweep(), third.sweep())
    assert sorted(taken) == [0, 1]
    release.set()


@pytest.mark.anyio
async def test_a_question_taken_over_is_still_stopped_by_its_asker(backends, platform):
    kind, ((old, before), (new, after)) = backends
    release = threading.Event()
    model = platform([{"text": ANSWER}], rest_held_until=release)
    await _begin(old, kind)

    await _first_words(before, model)
    await old.let_go()
    assert await new.sweep() == 1
    after.stop = True
    await new._host.stop(Started(7).ref)
    release.set()
    await _until(lambda: _ended(after))

    assert after.ends[0]["stopped"] is True


@pytest.mark.anyio
async def test_the_slot_a_question_holds_stays_held_and_is_let_go_at_its_end(
    backends, platform, valkey
):
    kind, ((old, before), (new, after)) = backends
    redis = valkey()
    hold = f"test-hold:{uuid.uuid4()}"
    slot = await admission.enter(
        redis, str(uuid.uuid4()), hold=admission.Hold(hold), wait_s=0
    )
    assert slot is not None
    release = threading.Event()
    model = platform([{"text": ANSWER}], rest_held_until=release)
    await _begin(old, kind, slots=[slot])

    await _first_words(before, model)
    await old.let_go()
    assert await new.sweep() == 1
    # Nobody else may take the conversation while the answer goes on.
    other = await admission.enter(redis, "other", hold=admission.Hold(hold), wait_s=0)
    assert other is None
    release.set()
    await _until(lambda: _ended(after))

    # The answer is kept before the slot is let go, so the end shows first.
    async def let_go() -> bool:
        return await redis.exists(hold) == 0

    await _until(let_go, timeout=10.0)


@pytest.mark.anyio
async def test_a_viewer_reconnecting_from_where_it_was_gets_only_the_rest(
    backends, platform
):
    kind, ((old, before), _) = backends
    release = threading.Event()
    model = platform([{"text": ANSWER}], rest_held_until=release)
    work = await _begin(old, kind)

    # The rest is written only once the first words were told, so the answer
    # reaches viewers in more than one piece wherever a slow reader picks it up.
    await _first_words(before, model)
    release.set()
    await _until(lambda: _ended(before))
    consumption = await old.get(work)
    assert consumption is not None

    seen = [entry async for entry in old.watch(work)]
    middle = seen[len(seen) // 2][0]
    rest = [entry async for entry in old.watch(work, middle)]

    assert rest == seen[len(seen) // 2 + 1 :]
    assert rest[-1][1] == "done"


class Away:
    """The session host as a process it is not connected to sees it."""

    def __init__(self, hub) -> None:
        self._hub = hub

    def is_online(self, device_id: str) -> bool:
        return False

    def __getattr__(self, name):
        return getattr(self._hub, name)


@pytest.mark.anyio
async def test_only_a_backend_the_session_host_is_connected_to_takes_a_question_up(
    backends, platform, hub, tmp_path, valkey
):
    kind, ((old, before), (new, after)) = backends
    release = threading.Event()
    model = platform([{"text": ANSWER}], rest_held_until=release)
    await _begin(old, kind)
    away = Consumptions(
        SessionHost(Away(hub), mirrors=tmp_path / "mirrors"), valkey, me="away"
    )
    away.serve(kind, Recorder())

    await _first_words(before, model)
    await old.let_go()

    assert await away.sweep() == 0
    assert await new.sweep() == 1
    release.set()
    await _until(lambda: _ended(after))
    assert [end["answer"].text for end in after.ends] == [ANSWER]


@pytest.mark.anyio
async def test_a_question_ends_once_even_when_its_reader_died_tidying_up(
    backends, platform, valkey
):
    kind, ((old, before), (new, after)) = backends
    platform([{"text": ANSWER}])
    work = await _begin(old, kind)
    await _until(lambda: _ended(before))

    # Its consumer hears the end before the reader puts the question away and
    # drops its lease: wait for the reader to be done with it.
    async def read() -> bool:
        return work not in old._reading

    await _until(read)
    # Died after the answer was kept, before the question was put away.
    await valkey().sadd("consumptions", work)

    assert await new.sweep() == 1
    await _until(lambda: _gone(valkey(), work))
    assert after.ends == []
    assert len(before.ends) == 1


async def _gone(redis, work: str) -> bool:
    return not await redis.sismember("consumptions", work)


class Gone:
    """The session host as the connector answers once the runner is gone: the
    machine is online, and what the call needs is not there."""

    def __init__(self, hub, why) -> None:
        self._hub = hub
        self._why = why

    async def call_executor(self, device_id, state, method, params, **kwargs):
        raise DeviceCallError(self._why(state))

    def __getattr__(self, name):
        return getattr(self._hub, name)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "why",
    [
        lambda state: (
            "dial unix /tmp/cheese-execution-1000-0c5e4b916d2a.sock: "
            "connect: no such file or directory"
        ),
        lambda state: f"lstat {state}: no such file or directory",
    ],
    ids=["socket", "state-directory"],
)
async def test_a_question_whose_runner_is_gone_ends_instead_of_being_taken_up_again(
    backends, platform, hub, tmp_path, valkey, why
):
    kind, ((old, before), _) = backends
    release = threading.Event()
    platform([{"text": ANSWER}], rest_held_until=release)
    work = await _begin(old, kind)
    gone = Consumptions(
        SessionHost(Gone(hub, why), mirrors=tmp_path / "mirrors"), valkey, me="gone"
    )
    after = Recorder()
    gone.serve(kind, after)
    # Let go at once rather than after the first words: what the session does
    # next is the runner's load, and an answer that finishes on its own would
    # leave no question for the sweep to take up.
    await old.let_go()

    assert await gone.sweep() == 1
    await _until(lambda: _ended(after))
    await _until(lambda: _gone(valkey(), work))
    assert len(after.ends) == 1 and after.ends[0]["failure"] is not None
    assert await gone.sweep() == 0
    release.set()
