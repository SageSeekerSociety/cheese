"""Member activity: which member is doing something in which room, right now.

A room has no status of its own. What it has is members, and a member can be
busy in it: a person composing in the room's input is ``typing``, an agent with
a turn running there is ``working``. Both are the same kind of fact — automatic,
ephemeral, attributed to one member in one room — so both are kept here, in one
shape, and reach the room the same way (an ``activity`` frame on its channel,
and the ``activity_snapshot`` a socket gets on connect).

Nothing here is persisted. Typing lives for ``TYPING_TTL_S`` past the last ping;
working lives exactly as long as that agent has a live turn on the channel,
which the broker learns from the turn frames it already relays.
"""

import time

#: How long one typing ping keeps a person "typing". The client pings every
#: three seconds while the input changes, so the mark goes within about five
#: seconds of the last keystroke, as Slack's does.
TYPING_TTL_S = 5.0

#: Frames that are standalone facts, not turn progress: fanned out live and
#: never buffered for a reconnect (`InProcessBroker.publish`). A 支线's
#: status on its channel's main line (`thread_status`) is one: replayed, it
#: would say a teammate is still queued long after it started.
LIVE_ONLY = (
    "reaction",
    "agent_control",
    "live",
    "activity",
    "comment_activity",
    "thread_status",
)

TYPING = "typing"
WORKING = "working"


def activity_frame(member: str, kind: str, active: bool, since: float) -> dict:
    frame = {
        "type": "activity",
        "member": member,
        "kind": kind,
        "active": active,
        "since": since,
    }
    if kind == TYPING and active:
        frame["expires_in"] = TYPING_TTL_S
    return frame


class RoomActivity:
    """Per channel: who is working (from live turns) and who is typing."""

    def __init__(self, turn_starts: dict[tuple[str, str], float]) -> None:
        # The broker's own record of when each live turn started (epoch s).
        self._turn_starts = turn_starts
        # (channel, turn id) → the agent seat running it. One agent can hold
        # several turns on a channel; it stops working when the last one ends.
        self._turn_agents: dict[tuple[str, str], str] = {}
        # channel → member → (typing since, expires at on the monotonic clock)
        self._typing: dict[str, dict[str, tuple[float, float]]] = {}
        # 支线 → its channel, learnt when a turn is assembled in the 支线.
        self._thread_rooms: dict[str, str] = {}

    def reset(self) -> None:
        """Between tests; the turn starts are the broker's to clear."""
        self._turn_agents.clear()
        self._typing.clear()
        self._thread_rooms.clear()

    def note_thread(self, thread_id, room_id) -> None:
        """This channel is a 支线 of that channel."""
        self._thread_rooms[str(thread_id)] = str(room_id)

    def told(self, channel: str, frame: dict | None) -> list[tuple[str, dict]]:
        """Where an activity change on ``channel`` is told: the channel itself,
        and when an AI teammate started or stopped working in a 支线, its
        channel's main line too. The line under the message the 支线 hangs
        under says who is answering; the main line does not hear the 支线's own
        frames."""
        if frame is None:
            return []
        room = self._thread_rooms.get(channel)
        if room is None or frame.get("kind") != WORKING:
            return [(channel, frame)]
        return [
            (channel, frame),
            (
                room,
                {
                    "type": "thread_activity",
                    "thread_id": channel,
                    "member": frame["member"],
                    "active": frame["active"],
                },
            ),
        ]

    def _working_since(self, channel: str, agent: str) -> float | None:
        starts = [
            self._turn_starts.get((c, turn_id), time.time())
            for (c, turn_id), who in self._turn_agents.items()
            if c == channel and who == agent
        ]
        return min(starts) if starts else None

    def turn_agents(self, channel: str) -> dict[str, str]:
        """Which seat each live turn on this channel is running on."""
        return {
            turn_id: agent
            for (c, turn_id), agent in self._turn_agents.items()
            if c == channel
        }

    def turn_started(self, channel: str, turn_id: str, agent: str) -> dict | None:
        """A live turn is known to be ``agent``'s. The frame to send, when this
        is what makes the agent start working here."""
        was = self._working_since(channel, agent)
        self._turn_agents[(channel, turn_id)] = agent
        if was is not None:
            return None
        since = self._working_since(channel, agent)
        return activity_frame(agent, WORKING, True, since or time.time())

    def turn_finished(self, channel: str, turn_id: str) -> dict | None:
        """A turn ended. The frame to send, when its agent has no other turn here."""
        agent = self._turn_agents.pop((channel, turn_id), None)
        if agent is None or self._working_since(channel, agent):
            return None
        return activity_frame(agent, WORKING, False, time.time())

    def typing(self, channel: str, member: str, active: bool) -> dict | None:
        """A typing ping (or its end). The frame to send, if anything changed."""
        room = self._typing.setdefault(channel, {})
        now = time.monotonic()
        current = room.get(member)
        live = current is not None and current[1] > now
        if not active:
            room.pop(member, None)
            if not room:
                self._typing.pop(channel, None)
            return activity_frame(member, TYPING, False, time.time()) if live else None
        since = current[0] if live and current else time.time()
        room[member] = (since, now + TYPING_TTL_S)
        return activity_frame(member, TYPING, True, since)

    def snapshot(self, channel: str) -> list[dict]:
        """Everyone active on this channel now, in the frames' entry shape."""
        entries: list[dict] = []
        for agent in sorted(set(self.turn_agents(channel).values())):
            since = self._working_since(channel, agent)
            entries.append({"member": agent, "kind": WORKING, "since": since})
        now = time.monotonic()
        for member, (since, expires) in sorted(self._typing.get(channel, {}).items()):
            if expires > now:
                entries.append(
                    {
                        "member": member,
                        "kind": TYPING,
                        "since": since,
                        "expires_in": round(expires - now, 1),
                    }
                )
        return entries
