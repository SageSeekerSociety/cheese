"""The document's 芝士 always writes with the writing guide in front of it.

Every session it runs writes into a document, so the guide is part of the
prompt rather than a skill it may skip; a block it writes in the wrong shape is
refused, and the refusal only helps if the right shape is already there.
"""

from app.domain.agent.document import question as doc_question


def test_every_document_session_is_given_the_block_syntax():
    for where in ("thread", "box"):
        prompt = doc_question.system_prompt("芝士", None, None, where=where)
        for spelling in (":::timeline", ":::stats", "> [!IMPORTANT]", "{✓ "):
            assert spelling in prompt, (where, spelling)
