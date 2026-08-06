"""The decision half of usage control: refuse a turn that cannot be afforded.

Reading a transcript afterwards (see ``CHEESE_USAGE_READER``) tells you what a
turn cost once it is over. That is enough to answer "where did the money go",
which is what was missing, but it cannot stop the turn that empties the account
— by the time the number exists, it has been spent.

Enforcement therefore has to sit in the request path. What sits there differs by
provider, and the difference is not cosmetic:

  * an API-key provider is fronted by our gateway, which already refuses a
    virtual key past its ``max_budget``. Nothing more is needed there.
  * a subscription is reached through a TRANSPARENT proxy, because its
    legitimacy depends on the client being Claude Code itself. A proxy may drop
    a connection; it must not rewrite the request, or the fingerprint changes
    and the account — a person's — is what pays for it.

So the check below is deliberately a yes/no on the *connection*, before any
bytes are forwarded. It never inspects or alters a request body.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetState:
    """What a project has spent and what it was allowed, in USD."""

    spent_usd: float
    limit_usd: float | None  # None = unlimited (自治项目, spec §9.1)


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str


def decide(state: BudgetState) -> Decision:
    """Whether to let one more turn through.

    Refusing is a visible failure: the turn does not start, and the caller is
    told why. That is the whole point — the alternative is the one this codebase
    keeps producing, where the work happens, the money goes, and everything
    reports success.
    """
    if state.limit_usd is None:
        return Decision(True, "unlimited")
    if state.spent_usd < state.limit_usd:
        remaining = state.limit_usd - state.spent_usd
        return Decision(True, f"${remaining:.4f} of budget remaining")
    return Decision(
        False,
        f"budget spent: ${state.spent_usd:.4f} of ${state.limit_usd:.4f}",
    )


def should_refuse_connection(state: BudgetState) -> bool:
    """The transparent proxy's only question.

    A refusal closes the connection. It does not send a synthetic error body:
    inventing a response the upstream never produced is exactly the kind of
    quiet substitution that makes a failure look like an answer.
    """
    return not decide(state).allow
