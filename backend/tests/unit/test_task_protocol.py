"""机构协议 resolution — 项目集 terms, with a per-赛题 override (#370 option (c)).

The placement is the decision these pin. 创研课 2026 秋 has twenty 赛题 and one
set of terms, so the terms live on the 项目集 and a teacher configures them once;
the override exists for the single 赛题 that genuinely differs. Getting that
backwards means either twenty copies to keep in step, or no way to say "this one
gets more compute".
"""

from types import SimpleNamespace

from app.domain.task.protocol import Protocol, resolve


def _category(**kw) -> SimpleNamespace:
    return SimpleNamespace(
        resource_pack=kw.get("resource_pack", {}),
        conditions=kw.get("conditions", []),
        default_role=kw.get("default_role"),
    )


def _task(override=None) -> SimpleNamespace:
    return SimpleNamespace(protocol_override=override)


def test_a_task_inherits_its_category_terms() -> None:
    got = resolve(
        category=_category(
            resource_pack={"compute_credits": 500},
            conditions=[{"required_topic": "结题答辩", "reviewer_role": "mentor"}],
            default_role="academic",
        ),
        task=_task(),
    )

    assert got.compute_credits == 500
    assert got.default_role == "academic"
    assert got.mentor_required_for("结题答辩") is True


def test_an_override_replaces_one_key_and_leaves_the_rest() -> None:
    """Whole-key replacement, not a deep merge.

    A half-inherited resource pack — some keys from the 项目集, some from the
    赛题 — is harder to reason about than either source alone, and impossible to
    show honestly in a form.
    """
    got = resolve(
        category=_category(
            resource_pack={"compute_credits": 500}, default_role="academic"
        ),
        task=_task({"resource_pack": {"compute_credits": 2000}}),
    )

    assert got.compute_credits == 2000  # overridden
    assert got.default_role == "academic"  # still inherited


def test_no_category_and_no_task_is_an_empty_protocol_not_an_error() -> None:
    """项目自治 (spec §4): a project belonging to no 赛题 has no terms, and that
    is the default rather than a missing configuration."""
    got = resolve(category=None, task=None)

    assert got == Protocol()
    assert got.compute_credits == 0
    assert got.mentor_required_for("任何话题") is False


def test_an_empty_required_topic_matches_nothing() -> None:
    """It used to match everything, turning one blank field in a form into
    "every topic in this project needs a mentor" — a footgun the teacher who
    left it blank cannot see."""
    got = resolve(
        category=_category(
            conditions=[{"required_topic": "", "reviewer_role": "mentor"}]
        ),
        task=_task(),
    )

    assert got.mentor_required_for("随便什么话题") is False


def test_a_condition_for_another_topic_does_not_bind_this_one() -> None:
    got = resolve(
        category=_category(
            conditions=[{"required_topic": "结题答辩", "reviewer_role": "mentor"}]
        ),
        task=_task(),
    )

    assert got.mentor_required_for("中期检查") is False


def test_true_is_not_one_credit() -> None:
    """`True` is an int in Python, so a resource pack carrying `true` would
    otherwise issue a one-credit grant — and a project with ANY grant is metered
    (spec §4), so that flips a project from unlimited to nearly-zero."""
    got = resolve(
        category=_category(resource_pack={"compute_credits": True}), task=_task()
    )

    assert got.compute_credits == 0


def test_a_negative_or_zero_pack_grants_nothing() -> None:
    for value in (0, -100):
        got = resolve(
            category=_category(resource_pack={"compute_credits": value}), task=_task()
        )
        assert got.compute_credits == 0
