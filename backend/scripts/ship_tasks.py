"""Release the tasks a dev deploy put live. Run by the dev deploy.

    python -m scripts.ship_tasks --repository-url https://github.com/OWNER/REPO \\
      --sha <the commit now live> < commits.json

`commits.json` is the same list `scripts/ship_feedback.py` reads: GitHub's
compare endpoint's commits between the release replaced and the one now live,
each with the time its PR merged — `[{"sha", "message", "merged_at"}]`.
`.github/workflows/deploy-dev.yml` builds it. A task whose last delivery merged
and waits for its deploy is closed (by its main line's conclusion, or after
its teammate's write-up) once one of these commits is
its merge (`app/domain/review/deploy_wait.py`). Every run is kept as the
repository's latest deploy report, which is what keeps later merges waiting
for their deploy. Prints one line per task released. Safe to run twice: a task
already released is not waiting any more.
"""

import argparse
import asyncio
import json
import sys

from app.core.db import async_session_factory
from app.domain.agent.announce import announce
from app.domain.agent_instance.adapters import task_teammate
from app.domain.delivery.agent import record_seat_instruction, record_task_instruction
from app.domain.review.deploy_wait import ship


async def main(repository_url: str, sha: str, commits: list[dict]) -> None:
    released = await ship(
        async_session_factory,
        commits,
        repository_url=repository_url,
        sha=sha,
        record_instruction=record_task_instruction,
        task_teammate=task_teammate,
        announce=announce,
        record_seat=record_seat_instruction,
    )
    for task_id in released:
        print(f"task {task_id} deployed")
    if not released:
        print(f"no waiting task in {len(commits)} commit(s)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository-url", required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.repository_url, args.sha, json.load(sys.stdin)))
