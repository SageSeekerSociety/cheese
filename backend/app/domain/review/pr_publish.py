"""Open a real GitHub PR for a green accept card (PR-based accept, #188 §5.1).

Dispatched fire-and-forget when a card turns `pending` (born pending on
projects without a gate, or promoted by a green gate). Pushes the topic branch
to the upstream and opens (or finds) the PR, then records pr_number/pr_url on
the card. The background dispatch is best-effort: any failure leaves the card
PR-less. Acceptance does NOT fall back to a local merge for such a card any
more — the accept path retries this publish synchronously via
`open_pr_for_card` and stops, visibly, if opening the PR still fails
(AcceptService._publish_pr_for_accept): a merge commit direct-pushed to main
with no PR is exactly what #296 exists to end.

Same task-reference pattern as review/gate.py.
"""

import asyncio
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.domain.agent.github_app import github_app_tokens_for_project
from app.domain.review import notes
from app.domain.review.github_pr import GitHubPRClient, parse_github_repo
from app.domain.workspace import service as ws

logger = logging.getLogger("cheesex.pr_publish")

#: 开 PR 失败落卡 (#362 修法 1)。一张开 PR 失败的卡必须和「PR 还在路上」的卡长得
#: 明显不同——这条前缀就是那个不同：失败原因直接写在 note 上，而不是只进 logger。
#: 采纳现场的补开（AcceptService._publish_pr_for_accept）就是它的重试路径；重试
#: 开出 PR 后 `record_pr` 会把这条 note 清掉。
PR_OPEN_FAILED_PREFIX = "开 PR 失败"

_TASKS: set[asyncio.Task] = set()


def enabled() -> bool:
    """Cheap pre-check: the GitHub App is configured at all. The per-project
    eligibility (which installation, upstream is a GitHub https remote) is
    resolved in the task itself — it needs the DB and a subprocess."""
    return bool(settings.github_app_id) and bool(settings.github_app_private_key_path)


def dispatch(
    session_factory: async_sessionmaker,
    *,
    card_id: uuid.UUID,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
) -> None:
    """Start the PR publication in the background; returns immediately."""
    task = asyncio.create_task(
        _run(
            session_factory,
            card_id=card_id,
            topic_id=topic_id,
            project_id=project_id,
        )
    )
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def _run(
    session_factory: async_sessionmaker,
    *,
    card_id: uuid.UUID,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
) -> None:
    try:
        pr = await _publish(
            session_factory,
            card_id=card_id,
            topic_id=topic_id,
            project_id=project_id,
        )
    except Exception as exc:  # noqa: BLE001 — card stays PR-less, but never silently (#362)
        logger.exception("PR publication failed for card %s", card_id)
        await _record_failure(session_factory, card_id=card_id, exc=exc)
        return
    if pr is None:
        return
    await record_pr(session_factory, card_id=card_id, pr=pr)


async def _publish(
    session_factory: async_sessionmaker,
    *,
    card_id: uuid.UUID,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
) -> dict | None:
    async with session_factory() as session:
        return await open_pr_for_card(
            session, card_id=card_id, topic_id=topic_id, project_id=project_id
        )


async def open_pr_for_card(
    session: AsyncSession,
    *,
    card_id: uuid.UUID,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
) -> dict | None:
    """Push the topic branch and open (or adopt) its App PR — the shared core
    of the fire-and-forget publish above and the accept-time publish for
    legacy PR-less cards (AcceptService._publish_pr_for_accept).

    Returns the PR json, or None when the PR path is NOT APPLICABLE to this
    card: no App installation for the project, a non-GitHub upstream, or a
    discussion-only topic with no branch to put in a PR. Raises when opening
    the PR FAILED — the two callers treat that differently (background: log
    and leave the card PR-less; accept: stop the accept, never direct-merge).
    Only ever reads through `session`; the card row is written by whoever
    owns it (`record_pr` below, or the accept transaction)."""
    # #192: the installation is resolved from this card's project, not a global.
    tokens = await github_app_tokens_for_project(project_id, session)
    if tokens is None:
        return None
    upstream = await asyncio.to_thread(ws.get_upstream, project_id)
    parsed = parse_github_repo(upstream)
    if parsed is None:
        return None  # not a GitHub https upstream — PR path not applicable
    owner, repo_name = parsed
    if not await asyncio.to_thread(ws.topic_branch_exists, project_id, topic_id):
        return None  # discussion-only topic — nothing a PR could carry

    token, _ = await tokens.write_token()
    branch = await asyncio.to_thread(ws.push_topic_branch, project_id, topic_id, token)
    base = (
        await asyncio.to_thread(
            lambda: ws.upstream_default_branch(ws.ensure_repo(project_id), token=token)
        )
        or ws.DEFAULT_BRANCH
    )

    title, body = await _pr_text(
        session, card_id=card_id, topic_id=topic_id, branch=branch
    )
    client = GitHubPRClient(owner, repo_name, tokens)
    pr = await client.open_pr(
        head=branch,
        base=base,
        title=title,
        body=body,
        # Open it as the person whose work it is, not as the bot — see
        # `GitHubPRClient.open_pr`. Push above still uses the App token: pushing
        # is not attributed to anyone, and the App's write access is the one
        # thing here that is guaranteed to work.
        as_user_token=await _requester_token(session, topic_id),
    )
    # The PR very often already exists by now: the batch's draft PR was opened
    # at its first commit (#718 拍板①) and `open_pr` adopts it rather than
    # failing on GitHub's "already exists". An adopted PR still carries the
    # placeholder words the draft opened with, so the card's own subject/body
    # has to be written onto it — otherwise the reviewer reads 「WIP: 房间名」
    # while the squash commit says something else entirely.
    await sync_pr_text(client, pr, title=title, body=body)
    # 递卡的语义就是「请人来看」，所以卡一递出去，PR 就不再是 draft (#718 拍板①)。
    # Not best-effort: a card that says 等验收 while GitHub still says 草稿 is a
    # delivery nobody can review, and nothing else would ever say so.
    if pr.get("draft") and pr.get("node_id"):
        await client.mark_ready_for_review(str(pr["node_id"]))
        pr["draft"] = False
    logger.info(
        "PR #%s ready for card %s (%s)", pr.get("number"), card_id, pr.get("html_url")
    )
    return pr


async def sync_pr_text(
    client: GitHubPRClient, pr: dict, *, title: str, body: str
) -> None:
    """Make the PR on GitHub say what `title`/`body` say — and only then.

    Skipping the write when it would change nothing is not an optimisation: a
    PATCH to a PR is an edit event on GitHub, and re-issuing it on every accept
    poll would fill the timeline with edits that changed no character.
    """
    if pr.get("title") == title and (pr.get("body") or "") == body:
        return
    number = pr.get("number")
    if number is None:
        return
    updated = await client.update_pr(int(number), title=title, body=body)
    pr.update(updated)


async def sweep_draft_prs(session_factory: async_sessionmaker) -> dict[str, int]:
    """有东西就有 PR (#718 拍板①): open a draft PR for every batch that has
    commits and no PR yet.

    **Why a sweep and not a hook on the commit.** The platform never sees the
    commit. A 分身 commits inside the shared worktree — no push, no webhook, no
    tool the platform can intercept — so there is no event to hang this on, only
    a fact to observe: the batch's branch is ahead of main. Observing it also
    makes the answer indifferent to HOW the commit arrived (in the worktree,
    over `git push` to `git_http`, by a human on the host), which an interception
    at any one of those three could never be.

    **The cost is latency, and here is its actual bound.** A first commit waits
    at most `accept_pr_poll_interval_s` (the tick this shares with the PR
    poller — one clock for the two things that watch PRs, rather than a second
    knob to get wrong) PLUS the time this pass spends on the trees ahead of it,
    because the trees are walked one at a time and each one that qualifies costs
    a branch check, a push and two GitHub round trips. So the bound grows
    LINEARLY with the number of open batches that have commits and no PR, and
    quoting the interval alone would understate it. In practice that number is
    tiny — a batch acquires its PR on the first tick after its first commit and
    then never qualifies again, so the steady state is "the batches that started
    in the last tick", not "every open batch". It would become a problem if a
    project ever had hundreds of rooms committing for the first time inside one
    interval; at that point this wants batching by project, not a shorter tick.

    Per-tree isolation, in a session of its own. A shared session would not
    merely lose one tree's work: a failed write leaves the transaction dirty, so
    every tree after it fails too — one project's missing App installation would
    silently cost every other room its PR, which is exactly the shape of failure
    a sweep exists to prevent.
    """
    from app.domain.room_task.services import WorkTreeService

    counts = {"opened": 0, "skipped": 0, "failed": 0}
    if not enabled():
        return counts
    async with session_factory() as session:
        wanted = [t.id for t in await WorkTreeService(session).open_without_pr()]
    for tree_id in wanted:
        try:
            async with session_factory() as session:
                opened = await _draft_pr_for_one_tree(session, tree_id)
                await session.commit()
        except Exception:  # noqa: BLE001 — one bad tree must not end the sweep
            counts["failed"] += 1
            logger.warning("draft PR not opened for tree %s", tree_id, exc_info=True)
            continue
        counts["opened" if opened else "skipped"] += 1
    return counts


async def _draft_pr_for_one_tree(session: AsyncSession, tree_id: uuid.UUID) -> bool:
    """Open and record this batch's draft PR. False = there was nothing to do.

    The row is re-read and LOCKED first, and that lock is the whole
    concurrency story. The list this sweep is walking was taken earlier and is a
    snapshot: by the time a tree's turn comes, its batch may have been delivered
    and merged — and opening a PR then would put a PR on a branch that is
    already squashed into main, which is a PR nobody can close by merging it.

    `FOR UPDATE` makes that deterministic rather than unlikely. Marking a tree
    merged (`AcceptService._mark_cards_tree_merged`) updates this row, so the two
    serialise on it whichever arrives first: an accept already in flight makes
    this wait and then see `merged`; a sweep already in flight makes the accept
    wait and then merge a batch that legitimately gained a PR a moment earlier.

    Same re-check answers the idempotence question: two overlapping passes over
    one tree cannot both open a PR, because the second sees `pr_number` set. Even
    if it somehow did, `open_pr` adopts the PR already open on that head instead
    of creating a second — but that is the belt, and this is the braces.
    """
    from app.domain.room_task.services import WorkTreeService

    trees = WorkTreeService(session)
    tree = await trees.claim_for_pr(tree_id)
    if tree is None:
        return False
    pr = await _open_draft_for_tree(session, tree)
    if pr is None:
        return False
    await trees.record_pr(
        tree, number=int(pr["number"]), url=str(pr.get("html_url") or "")
    )
    return True


async def _open_draft_for_tree(session: AsyncSession, tree) -> dict | None:  # noqa: ANN001
    """The draft PR for one batch, or None when this batch cannot have one yet.

    None (not an error) for: a project with no App installation, a non-GitHub
    upstream, and — the ordinary case, on every tick — a branch with nothing on
    it. 有东西才有 PR: an empty batch is the state a room sits in between
    deliveries, and opening a PR for it would put an empty diff in front of a
    reviewer.
    """
    from app.domain.review import pr_text
    from app.domain.room_task.place import PlaceResolver
    from app.domain.workspace import identity

    project_id = tree.project_id
    tokens = await github_app_tokens_for_project(project_id, session)
    if tokens is None:
        return None
    upstream = await asyncio.to_thread(ws.get_upstream, project_id)
    parsed = parse_github_repo(upstream)
    if parsed is None:
        return None
    branch = ws.branch_for_tree(tree.id)
    if not await asyncio.to_thread(ws.branch_has_commits, project_id, branch):
        return None
    place = await PlaceResolver(session).resolve(tree.room_id)
    if place is None:
        return None
    room = place.room

    token, _ = await tokens.write_token()
    await asyncio.to_thread(ws.push_branch, project_id, branch, token)
    base = (
        await asyncio.to_thread(
            lambda: ws.upstream_default_branch(ws.ensure_repo(project_id), token=token)
        )
        or ws.DEFAULT_BRANCH
    )
    who = await identity.attribution(session, room)
    client = GitHubPRClient(*parsed, tokens)
    pr = await client.open_pr(
        head=branch,
        base=base,
        # No `Reviewed-by` and no card: nobody has accepted, and the subject
        # this change will land under is not written until somebody files a
        # card. `WIP:` says both — and 递卡 replaces it (`sync_pr_text`).
        title=f"WIP: {room.title or branch}"[:255],
        body=pr_text.pr_body(room, "", None, who),
        as_user_token=await _requester_token(session, room.id),
        draft=True,
    )
    logger.info(
        "draft PR #%s opened for tree %s (%s)",
        pr.get("number"),
        tree.id,
        pr.get("html_url"),
    )
    return pr


async def _requester_token(session: AsyncSession, topic_id: uuid.UUID) -> str | None:
    """The GitHub credential of the human this topic belongs to, so the PR is
    opened in their name. None whenever they have not connected GitHub, their
    token cannot be refreshed, or anything at all goes wrong — this is an
    attribution nicety and must never be the reason a PR fails to open.

    Who that human is comes from `identity.requester_handle`, not from
    `Topic.created_by`: on a 分身-split room the creator is the 分身's own
    `cheese-<hex12>` handle, which matches no account, so this returned None and
    every such PR opened as `cheesex-app[bot]`."""
    from app.domain.oauth.services import get_github_user_token_for_handle
    from app.domain.room_task.place import PlaceResolver
    from app.domain.workspace import identity

    try:
        place = await PlaceResolver(session).resolve(topic_id)
        if place is None:
            return None
        handle = await identity.requester_handle(session, place.room)
        if not handle:
            return None
        return await get_github_user_token_for_handle(session, handle)
    except Exception:  # noqa: BLE001
        logger.info("no requester token for topic %s", topic_id, exc_info=True)
        return None


async def _pr_text(
    session: AsyncSession,
    *,
    card_id: uuid.UUID,
    topic_id: uuid.UUID,
    branch: str,
) -> tuple[str, str]:
    """PR title/body from the card's change summary (`cheese accept-request
    --subject/--body`), falling back to the topic title when the card was filed
    without one.

    Same builders the merge path uses (`review/pr_text.py`), on purpose: the
    PR a reviewer reads and the squash commit that lands on main must not be
    able to say two different things about the same change.

    The routing bookkeeping the old body carried — 验收人, 路由理由, "采纳这张
    验收卡即合并本 PR" — is gone. It described the platform's workflow to people
    who were already inside it, while the reviewer opening the PR on GitHub
    wanted to know what changed and why."""
    from app.domain.review import pr_text
    from app.domain.review.repositories import AcceptCardRepository
    from app.domain.room_task.place import PlaceResolver
    from app.domain.workspace import identity

    place = await PlaceResolver(session).resolve(topic_id)
    card = await AcceptCardRepository(session).get(card_id)
    if place is None:
        return branch, f"Cheese-Topic: {topic_id}"
    topic = place.room
    who = await identity.attribution(session, topic, card=card)
    # No approver yet — the PR opens when the card is FILED, and 采纳 is what
    # merges it. `Reviewed-by` is written onto the squash commit at merge time,
    # by whoever actually clicks.
    return pr_text.change_subject(card, topic), pr_text.pr_body(topic, "", card, who)


async def record_pr(
    session_factory: async_sessionmaker, *, card_id: uuid.UUID, pr: dict
) -> None:
    """Write the opened PR onto the card. Clears a `PR_OPEN_FAILED_PREFIX`
    note from an earlier failed publish — the card rides a PR now, and a
    stale「开 PR 失败」would contradict the pr_number sitting next to it."""
    from app.domain.review.repositories import AcceptCardRepository

    async with session_factory() as session:
        card = await AcceptCardRepository(session).get(card_id)
        if card is None:
            logger.error("PR recorded nowhere: card %s vanished", card_id)
            return
        card.pr_number = int(pr["number"])
        card.pr_url = str(pr.get("html_url") or "")[:255] or None
        if card.note_code is notes.NoteCode.pr_open_failed:
            notes.clear(card)
        await session.commit()


async def _record_failure(
    session_factory: async_sessionmaker, *, card_id: uuid.UUID, exc: BaseException
) -> None:
    """#362 修法 1: a failed publish lands ON THE CARD, not only in a log no
    one reads. A PR-less card used to be indistinguishable from one whose PR
    simply hadn't landed yet, and the accept path then slid into the local
    merge without anyone knowing the PR step had failed at all. Accepting the
    card retries the publish (AcceptService._publish_pr_for_accept), which
    closes the loop: fail visibly here, retry at accept, and the retry's own
    outcome replaces this note. Best-effort: a DB hiccup here must not raise
    out of the background task."""
    from app.domain.review.repositories import AcceptCardRepository

    note = (
        f"{PR_OPEN_FAILED_PREFIX}（{str(exc)[:300]}）。这张卡目前没有 PR；"
        "点采纳会现场重开 PR，开不出来采纳会停下，不会静默直推上游。"
    )[:2000]
    try:
        async with session_factory() as session:
            card = await AcceptCardRepository(session).get(card_id)
            if card is None:
                return
            notes.record(card, notes.NoteCode.pr_open_failed, note)
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("PR publish failure not recorded on card %s", card_id)
