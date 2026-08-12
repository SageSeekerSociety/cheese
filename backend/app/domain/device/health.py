"""When is a compute machine "dead enough" to stop sending work to it? (#186)

Pure decision logic: no DB, no ambient clock, no I/O — the caller passes the
current health record, the failure code and ``now``, and gets back what the new
record should be. That makes the judgement itself testable without a machine, a
database or a failing turn.

The standard (issue #186 §五, decided):

* **Per DEVICE, not per topic.** A full disk is a property of the machine, so
  every topic on that box is about to hit it. Counting per topic would make each
  topic rediscover the same dead machine from zero.
* **Two consecutive failures with the SAME code**, with no successful turn in
  between. One is a hiccup; the second is a pattern. The cost of the extra strike
  is one turn, and it buys immunity from single-sample flukes.
* **Any success resets the streak** — that is what "consecutive" means, and it is
  the machine's way out without anyone intervening.
* **Only host-scoped codes get here at all** (see ``PlatformFailure.host_scoped``).
  A missing runtime image would follow the topic to any machine, so counting it
  would quarantine healthy boxes for a registry outage.
* **The verdict is QUARANTINE, not destroy.** Disk pressure self-heals; a
  cooldown lets the machine come back on its own, and destroying it is both
  expensive and the irreversible way to be wrong.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.device.repository import HostHealth

# Two strikes, then a cooldown long enough for the usual cause (another topic
# filling the disk) to have finished and been cleaned up, but short enough that a
# machine is not written off for the afternoon.
DEFAULT_FAILURE_THRESHOLD = 2
DEFAULT_QUARANTINE = timedelta(minutes=30)

# How long a strike stays "recent enough" to be part of a streak. Two failures an
# hour apart are not one dying machine; they are two incidents, and the machine
# very likely served good turns in between.
#
# This is also the safety net under the success path. Clearing the streak on a good
# turn is what "consecutive" normally means, but that clear is best-effort — the
# turn layer only pays for it when THAT process saw the machine fail, so a strike
# recorded before a restart has nobody left to clear it. Without this window such a
# strike would wait indefinitely to ambush the next unrelated failure.
DEFAULT_STREAK_WINDOW = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class Verdict:
    """What the machine's health record should become after this failure."""

    consecutive_failures: int
    last_failure_code: str
    last_failure_at: datetime
    quarantined_until: datetime | None

    @property
    def quarantined(self) -> bool:
        """Whether this failure is the one that took the machine out of rotation."""
        return self.quarantined_until is not None


def is_quarantined(health: HostHealth | None, now: datetime) -> bool:
    """Whether the machine is out of rotation right now. A lapsed cooldown counts
    as healthy again without anyone having to clear it — the row is left alone so
    the history stays readable, and the next success deletes it."""
    return (
        health is not None
        and health.quarantined_until is not None
        and health.quarantined_until > now
    )


def judge_failure(
    health: HostHealth | None,
    code: str,
    now: datetime,
    *,
    threshold: int = DEFAULT_FAILURE_THRESHOLD,
    cooldown: timedelta = DEFAULT_QUARANTINE,
    streak_window: timedelta = DEFAULT_STREAK_WINDOW,
) -> Verdict:
    """Fold one host-scoped failure into the machine's health.

    A streak continues only when the previous strike was the SAME code and is
    still recent: two *different* host-scoped failures in a row are two accidents
    rather than one dying machine, and a strike from an hour ago is history, not
    evidence about now.
    """
    if (
        health is not None
        and health.last_failure_code == code
        and health.last_failure_at is not None
        and now - health.last_failure_at <= streak_window
    ):
        streak = health.consecutive_failures + 1
    else:
        streak = 1
    if streak >= threshold:
        quarantined_until = now + cooldown
    else:
        # Not enough evidence yet — don't invent a quarantine, and don't drop one
        # that a previous verdict already imposed and that is still running.
        quarantined_until = health.quarantined_until if health is not None else None
    return Verdict(
        consecutive_failures=streak,
        last_failure_code=code,
        last_failure_at=now,
        quarantined_until=quarantined_until,
    )
