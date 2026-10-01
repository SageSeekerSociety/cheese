"""Timeline blocks and their persisted product rules.

Ask entry points: ``ask_groups.AskGroups`` owns atomic group rules;
``answer_submission.submit_answer`` owns locked single-answer rules and
``add_answer_wake`` owns timeline wake insertion. Callers authorize and commit.
``input_effects`` applies identity-verified, already-locked native input effects
inside delivery's settlement transaction. These entries do not dispatch or commit.
"""
