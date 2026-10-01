"""Keep the seat pin, but restore the early-check-only membership policy."""

import asyncio

import pytest


@pytest.fixture(autouse=True)
def omit_final_membership_checks(monkeypatch):
    import app.domain.agent.chat as chat_module

    require = chat_module.require_pinned_seat
    checked = set()

    async def early_only(session, topic, instance):
        key = (asyncio.current_task(), id(session), topic, instance)
        if key not in checked:
            checked.add(key)
            return await require(session, topic, instance)
        return None

    monkeypatch.setattr(chat_module, "require_pinned_seat", early_only)
