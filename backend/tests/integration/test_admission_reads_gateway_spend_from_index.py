"""Model admission reads what a project's gateway key has spent from an index.

`POST /llm/admission` keeps the key's budget in step with the project's grants:
spent ever plus what the project may still spend. "Spent ever" is a sum over
the project's gateway usage rows (`UsageRepository.gateway_spend`), asked on
every model request. `ix_resource_usage_gateway_spend` covers those rows with
`cost_usd`, so the sum is answered from the index alone; the query writes the
predicate as the same literal (`GATEWAY_ROWS`), so the planner can prove it for
a prepared statement and not only for a freshly planned one.
"""

import asyncio
import re
import uuid

from sqlalchemy import event, text

from app.domain.usage.services import UsageService
from tests.integration.conftest import post_project

_SPEND = re.compile(r"sum\(resource_usage\.cost_usd\)")


def test_admission_sums_gateway_spend_with_the_index_predicate(client):
    """The sum admission reads to price the key's budget
    (`queries._gateway_budget_target` -> `UsageService.project_credits`)."""
    project_id = post_project(client, json={"name": "P"}).json()["data"]["id"]
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    async def credits() -> dict:
        async with client.test_factory() as session:
            engine = session.bind.sync_engine
            event.listen(engine, "after_cursor_execute", _record)
            try:
                return await UsageService(session).project_credits(
                    uuid.UUID(project_id)
                )
            finally:
                event.remove(engine, "after_cursor_execute", _record)

    summary = asyncio.run(credits())
    assert "gateway_budget_usd" in summary
    spend = [s for s in statements if _SPEND.search(s)]
    assert len(spend) == 1, statements
    assert "route = 'gateway'" in spend[0], spend[0]

    # The statement as sent, its project bound: the planner takes the covering
    # index for it (sequential scans off, so a near-empty table cannot hide it).
    # The sum rides in the statement that reads the project's credit cap
    # (`CreditBudgets.project_caps`), whose other parameters are bound too.
    sql = re.sub(r"\$\d+::UUID", "CAST(:project AS uuid)", spend[0])
    sql = re.sub(r"\$\d+::INTEGER", "0", sql)
    sql = re.sub(r"\$\d+::TIMESTAMP WITH TIME ZONE", "now()", sql)

    async def plan() -> str:
        async with client.test_factory() as session:
            await session.execute(text("SET LOCAL enable_seqscan = off"))
            rows = await session.execute(
                text("EXPLAIN " + sql), {"project": project_id}
            )
            return "\n".join(rows.scalars())

    assert "ix_resource_usage_gateway_spend" in asyncio.run(plan())
