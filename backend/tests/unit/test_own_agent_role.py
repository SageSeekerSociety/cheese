"""What a member's own agent is told about whose it is."""

from app.domain.agent_instance.own import own_role


def test_a_personal_agent_is_told_its_owner_by_the_name_people_know() -> None:
    """The agent names its owner when it writes to others; it can use the name
    people know only if it was told that name, not just the login handle."""
    role = own_role("张三", owner_handle="zhangsan")

    assert "对主人张三负责" in role
