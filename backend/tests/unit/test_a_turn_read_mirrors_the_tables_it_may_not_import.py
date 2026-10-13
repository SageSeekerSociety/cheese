"""`run_record.turns` reads two tables that its layer may not import.

`run_record` sits at the bottom of `app.domain`'s seven layers and `agent` and
`block` sit above it: importing either is the upward dependency C5 refuses. So
`turns.py` spells the columns, the terminal failure codes and the two meta keys
out by hand, the way `run_record.admin` already reads `projects`. Nothing goes
red when the originals are renamed over there — the read keeps running and
quietly answers wrong — so these hold the copies against their homes.
"""

from app.domain.agent.models import AgentTurn
from app.domain.agent.platform_notices import (
    EVENT_DEPLOY_INTERRUPTED,
    EVENT_TURN_FAILED,
    EVENT_TURN_TIMEOUT,
)
from app.domain.block.indexed_rows import FAILED_TURN_EVENTS
from app.domain.block.models import (
    PROMPT_ATTEMPTS_META_KEY,
    PROMPTED_TURN_META_KEY,
    Block,
)
from app.domain.run_record import turns


def test_the_terminal_codes_cover_every_code_their_homes_name():
    """Wider than the room-card set on purpose (see the module docstring), but
    it still has to contain every code those two homes write."""
    named = {EVENT_TURN_FAILED, EVENT_TURN_TIMEOUT, EVENT_DEPLOY_INTERRUPTED}
    assert named <= set(turns.TURN_FAILURE_EVENTS)
    assert set(FAILED_TURN_EVENTS) <= set(turns.TURN_FAILURE_EVENTS)


def test_the_meta_keys_are_the_ones_the_prompt_writes():
    assert turns.PROMPTED_TURN_META_KEY == PROMPTED_TURN_META_KEY
    assert turns.PROMPT_ATTEMPTS_META_KEY == PROMPT_ATTEMPTS_META_KEY


def test_the_columns_are_the_ones_the_models_declare():
    """Same names and types — reading fewer columns is the point, reading a
    column the table does not have is not."""
    for literal, model in ((turns._turns, AgentTurn), (turns._blocks, Block)):
        real = model.__table__.c
        assert set(literal.c.keys()) <= set(real.keys())
        for name in literal.c.keys():
            assert type(literal.c[name].type) is type(real[name].type)
