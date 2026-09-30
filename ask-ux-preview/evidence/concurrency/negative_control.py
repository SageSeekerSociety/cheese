"""Run the actual overlap suite with only lock-read refreshing disabled."""
import sys

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

original = AsyncSession.execute


async def without_refresh(session, statement, *args, **kwargs):
    if (
        getattr(statement, "_for_update_arg", None) is not None
        and statement.get_execution_options().get("populate_existing")
    ):
        statement = statement.execution_options(populate_existing=False)
    return await original(session, statement, *args, **kwargs)


AsyncSession.execute = without_refresh
sys.exit(pytest.main([
    "tests/integration/test_ask_concurrency.py", "-q", "-p", "no:randomly",
    "--tb=short",
]))
