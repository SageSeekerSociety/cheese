"""Read live scheduler/session facts without treating history as liveness."""

import time
import uuid

from app.domain.agent.realtime.broker import InProcessBroker
from app.domain.agent.turn.state.execution import ExecutionState


class WorkStatus:
    def __init__(self, broker: InProcessBroker, state: ExecutionState) -> None:
        self._broker = broker
        self.state = state

    def topic_work(self, topic_id: uuid.UUID, timeout: float) -> dict | None:
        """Latest lifecycle record for this topic. `ceiling_s` is this turn's
        effective absolute ceiling (`timeout`, or the channel's own hard
        ceiling once its `turn_ceiling` frame has rescheduled the outer wrap —
        see `_execute`) and `near_ceiling` is a coarse "within the last 10
        minutes" flag — turn 活跃度检测 deliberately does NOT expose a live
        `budget_left_s` countdown any more: that figure was observed making the
        agent rush against what's only meant to be a wedged-turn safety net
        (dev, 2026-08-08).
        Ring-buffer-backed, so None after a restart or ~100 turns elsewhere."""
        key = str(topic_id)
        activity = self._broker.activity_snapshot(key)
        if activity is not None:
            for rec in reversed(self.state.recent):
                if rec.get("turn_id") != activity["turn_id"]:
                    continue
                out = dict(rec)
                out["status"] = "running"
                out["started_at"] = activity["started_at"]
                out["ceiling_s"] = round(rec.get("ceiling_s") or timeout)
                out["near_ceiling"] = False
                return out
            return {
                "turn_id": activity["turn_id"],
                "topic_id": key,
                "status": "running",
                "started_at": activity["started_at"],
                "ceiling_s": round(timeout),
                "near_ceiling": False,
            }
        for rec in reversed(self.state.recent):
            if rec["topic_id"] != key:
                continue
            out = dict(rec)
            ceiling_s = rec.get("ceiling_s") or timeout
            out["ceiling_s"] = round(ceiling_s)
            if rec["status"] == "running":
                elapsed = time.time() - rec["started_at"]
                out["near_ceiling"] = (ceiling_s - elapsed) < 600
            return out
        return None

    def current_turn_record(self, topic_id: uuid.UUID) -> dict | None:
        """The `_recent` entry for the turn this topic is running NOW, or None.

        Shared by `continuation_for` and `turn_author_for` so the two cannot
        disagree about which turn "now" means. `_recent` is a ring buffer of what
        turns *did*, so the newest entry for a topic is not necessarily live —
        hence the two guards: prefer the broker's own live turn id, and when the
        broker has none, accept the newest entry only while it still reads
        `running`."""
        key = str(topic_id)
        activity = self._broker.activity_snapshot(key)
        active_id = activity["turn_id"] if activity is not None else None
        for rec in reversed(self.state.recent):
            if rec["topic_id"] != key:
                continue
            if active_id is not None and rec.get("turn_id") != active_id:
                continue
            if active_id is None and rec["status"] != "running":
                return None
            return rec
        return None

    def running_topic_ids(self) -> set[uuid.UUID]:
        """Every topic with a turn currently in flight — for the board's bulk
        read, which can't afford one `topic_work()` lookup per row. Same "newest
        record per topic wins" rule as `topic_work()`, across all topics."""
        seen: set[str] = set()
        running: set[uuid.UUID] = set()
        for channel in self._broker.active_channels():
            try:
                running.add(uuid.UUID(channel))
            except ValueError:
                continue
        for rec in reversed(self.state.recent):
            key = rec["topic_id"]
            if key in seen:
                continue
            seen.add(key)
            if rec["status"] == "running":
                running.add(uuid.UUID(key))
        return running

    def live_work_for_topic(self, topic_id: uuid.UUID) -> dict | None:
        """The turn THIS process is actually executing for `topic_id`, with how
        long since it last published a frame — or None if nobody is running one.

        This is the heartbeat half of the stall verdict (see
        `TopicService.stall_signal`), and deliberately not `topic_work()`:
        `_recent` is a ring buffer of what turns *did*, so a turn killed with the
        process still reads `running` there forever. `_live` is emptied by the
        turn's own `finally`, which a dying process never gets to run — so a
        registry entry with no `_live` entry means the executor is gone, no
        matter what the buffer remembers.
        """
        activity = self._broker.activity_snapshot(str(topic_id))
        if activity is not None:
            return {
                "turn_id": activity["turn_id"],
                "silent_for_s": activity["idle_for_s"],
            }
        now = time.monotonic()
        for turn_id, live_topic in self.state.live_topics.items():
            if live_topic != topic_id or turn_id not in self.state.live:
                continue
            frame_at = self.state.last_frame_at.get(turn_id)
            return {
                "turn_id": turn_id,
                # None means the bookkeeping is off (an entry without a frame
                # stamp); the caller treats an unknown gap as "not proof of
                # life" rather than inventing a fresh one.
                "silent_for_s": None if frame_at is None else round(now - frame_at, 1),
            }
        return None
