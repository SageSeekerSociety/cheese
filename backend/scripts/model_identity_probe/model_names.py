"""Folding a provider's model id down to the part two names must share.

One home for the rule, so the wire comparison (``report.build_provenance``) and
the admission cross-check (``verdict.combine``) fold names the same way -- two
call sites with their own idea of "the same model" is how a header and a body
with the same name stop agreeing.
"""

from __future__ import annotations

import re

#: A trailing snapshot/date suffix a provider pastes onto a model id, like
#: ``claude-opus-5-5-20250915`` or ``gpt-6-astra-2026-05-01``. The claim the
#: platform holds ("claude-opus-5-5") is never dated, so a dated wire name is an
#: alias of the claim, not a different model.
_SNAPSHOT_SUFFIX = re.compile(r"[-_.]\d{4}(?:[-_.]?\d{2}){1,2}$")


def normalize_model_name(name: str | None) -> str | None:
    """Fold a model id down to the part that must agree.

    Strips a provider prefix (``openai/`` in ``openai/gpt-6-astra``) and a
    trailing dated snapshot, lowercases, and drops dots/underscores so
    ``gpt-6.1-sol`` and ``gpt-6-1-sol`` fold together. What is left is the
    family/stem a claim and a wire name have to share; anything past that
    (a version, a build) is what the alias rule handles.
    """
    if not name:
        return None
    base = name.strip().rsplit("/", 1)[-1]
    base = _SNAPSHOT_SUFFIX.sub("", base)
    base = base.lower().replace("_", "-").replace(".", "-")
    return base or None


def alias_ambiguous(claimed: str | None, wire: str | None) -> bool:
    """Whether two *different* normalized names could still be one model under
    an alias the tool cannot resolve: one is a token-boundary prefix of the
    other, e.g. ``claude-opus-5-5`` vs ``claude-opus-5-5-preview`` or
    ``gpt-6-astra`` vs ``gpt-6-astra-latest``. Those are reported uncertain,
    never a mismatch -- a naming a human has to settle is not a wrong identity.
    The boundary check keeps a genuine neighbour (``claude-opus-5-5`` vs
    ``claude-opus-5-50``) out of the alias bucket."""
    if not claimed or not wire or claimed == wire:
        return False
    shorter, longer = sorted((claimed, wire), key=len)
    if not longer.startswith(shorter):
        return False
    return longer[len(shorter)] == "-"
