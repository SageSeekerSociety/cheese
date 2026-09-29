"""Move the feedback a release fixes to `deployed`. Run by the dev deploy.

    gh api repos/OWNER/REPO/compare/OLD...NEW \\
        --jq '[.commits[] | {sha, message: .commit.message}]' \\
      | python -m scripts.ship_feedback --repository-url https://github.com/OWNER/REPO

The convention and the reasoning are in `app/domain/feedback/shipping.py`.
Prints one line per report named. Safe to run twice: a report already
`deployed` is left alone.
"""

import argparse
import asyncio
import json
import sys

from app.core.db import async_session_factory
from app.domain.feedback.shipping import ship


async def main(repository_url: str, commits: list[dict]) -> None:
    async with async_session_factory() as session:
        outcomes = await ship(session, commits, repository_url=repository_url)
        await session.commit()
    for outcome in outcomes:
        print(f"FB-{outcome.display_no} {outcome.result} {outcome.link}")
    if not outcomes:
        print(f"no feedback named in {len(commits)} commit(s)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository-url", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.repository_url, json.load(sys.stdin)))
