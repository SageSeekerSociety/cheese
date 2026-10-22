"""Mail every registered person the 「知是正在内测」 letter, once. Dry run by default.

    python -m scripts.send_beta_notice --state-dir /data/beta-notice            # dry run
    python -m scripts.send_beta_notice --state-dir /data/beta-notice --sample alice
    python -m scripts.send_beta_notice --state-dir /data/beta-notice --send     # mails

Who gets it: every live account (``deleted_at IS NULL``) whose address can receive
mail (not a placeholder, ``app.core.email.is_placeholder_email``) and that is a
person, not an agent identity (no row in ``agent_bindings``). Registration verifies
the address, so a live account with a real address is a verified one.

What each letter says is worked out for that person when it is written: their name
(profile nickname, else handle), what their own plan issues a month, and whether
they have claimed the 内测 credits yet. The figures come from the same code as the
in-app notice (``CreditIncentives.beta`` and the referral constants).

Resumable and idempotent. ``--state-dir`` holds one file per person mailed,
``sent/<user id>.json``, written right after the server accepted the message; a
rerun skips everyone with a file there, so a crash costs at most the one letter in
flight, and running it twice mails nobody twice. ``run.log`` in the same directory
gets a timestamped line when each person starts and when they end (sent, skipped and
why, or failed and why), flushed as it goes. A dry run writes the log and
``recipients.txt`` (user ids only) and mails nobody.

Sending to users is outward-facing: run with ``--send`` only once the final text has
been approved.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.core.db import async_session_factory
from app.core.email import get_email_sender, is_placeholder_email
from app.domain.identity.models import AgentBinding
from app.domain.notification.announcement import (
    SUBJECT,
    BetaOffer,
    render_html,
    render_text,
)
from app.domain.usage import referrals
from app.domain.usage.incentives import CreditIncentives
from app.domain.user.models import User, UserProfile


class Log:
    def __init__(self, path: Path) -> None:
        self._file = path.open("a", encoding="utf-8")

    def __call__(self, line: str) -> None:
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._file.write(f"[{stamp}] {line}\n")
        self._file.flush()
        print(line, flush=True)


async def recipients(session) -> list[tuple[User, str]]:
    """(account, display name) for everyone the letter goes to, by id."""
    agents = select(AgentBinding.user_id)
    users = list(
        await session.scalars(
            select(User)
            .where(User.deleted_at.is_(None), User.id.not_in(agents))
            .order_by(User.id)
        )
    )
    users = [u for u in users if not is_placeholder_email(u.email)]
    nicknames = dict(
        (
            await session.execute(
                select(UserProfile.user_id, UserProfile.nickname).where(
                    UserProfile.deleted_at.is_(None),
                    UserProfile.user_id.in_([u.id for u in users]),
                )
            )
        ).all()
    )
    return [(u, (nicknames.get(u.id) or "").strip() or u.username) for u in users]


async def offer_for(session, user: User, name: str) -> BetaOffer:
    beta = await CreditIncentives(session).beta(user.id)
    base = settings.frontend_url.rstrip("/")
    return BetaOffer(
        name=name,
        plan_credits=beta["plan_credits"],
        beta_credits=beta["credits"],
        beta_claimed=beta["claimed"],
        feedback_credits=beta["feedback_credits"],
        invite_credits=referrals.INVITER_CREDITS,
        invitee_credits=referrals.JOIN_CREDITS + referrals.ACTIVATED_CREDITS,
        credits_link=f"{base}/teams/{user.username}/credits",
    )


async def main(state_dir: Path, *, send: bool, sample: str | None) -> int:
    sent_dir = state_dir / "sent"
    sent_dir.mkdir(parents=True, exist_ok=True)
    log = Log(state_dir / "run.log")
    mode = "send" if send else "dry-run"
    async with async_session_factory() as session:
        people = await recipients(session)
        log(
            f"run start mode={mode} recipients={len(people)} "
            f"frontend={settings.frontend_url} subject={SUBJECT!r}"
        )
        if not send:
            (state_dir / "recipients.txt").write_text(
                "".join(f"{u.id}\n" for u, _ in people), encoding="utf-8"
            )
        if sample is not None:
            match = [(u, n) for u, n in people if u.username == sample]
            if not match:
                log(f"sample handle={sample} status=not_a_recipient")
                return 1
            user, name = match[0]
            offer = await offer_for(session, user, name)
            (state_dir / f"sample-{user.id}.html").write_text(
                render_html(offer), encoding="utf-8"
            )
            (state_dir / f"sample-{user.id}.txt").write_text(
                f"Subject: {SUBJECT}\n\n{render_text(offer)}\n", encoding="utf-8"
            )
            log(f"sample user={user.id} status=written")
        if not send:
            already = sum(1 for u, _ in people if (sent_dir / f"{u.id}.json").exists())
            log(
                f"run end mode=dry-run would_send={len(people) - already} already_sent={already}"
            )
            return 0

        sender = get_email_sender()
        counts = {"sent": 0, "skipped": 0, "failed": 0}
        for user, name in people:
            marker = sent_dir / f"{user.id}.json"
            log(f"user={user.id} status=start")
            if marker.exists():
                counts["skipped"] += 1
                log(f"user={user.id} status=skipped reason=already_sent")
                continue
            offer = await offer_for(session, user, name)
            try:
                ok = await sender.send(
                    to=user.email,
                    subject=SUBJECT,
                    body_html=render_html(offer),
                    body_text=render_text(offer),
                )
            except Exception as exc:  # one bad address must not stop the run
                counts["failed"] += 1
                log(f"user={user.id} status=fail reason={type(exc).__name__}:{exc}")
                continue
            if not ok:
                counts["failed"] += 1
                log(f"user={user.id} status=fail reason=smtp_returned_false")
                continue
            marker.write_text(
                json.dumps(
                    {
                        "user_id": user.id,
                        "sent_at": datetime.now(UTC).isoformat(),
                        "subject": SUBJECT,
                        "plan_credits": offer.plan_credits,
                        "beta_claimed": offer.beta_claimed,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            counts["sent"] += 1
            log(f"user={user.id} status=sent claimed={offer.beta_claimed}")
        log(f"run end mode=send {' '.join(f'{k}={v}' for k, v in counts.items())}")
        return 1 if counts["failed"] else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument(
        "--send", action="store_true", help="actually mail; dry run without it"
    )
    parser.add_argument(
        "--sample", help="also write the letter one recipient would get, by handle"
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.state_dir, send=args.send, sample=args.sample)))
