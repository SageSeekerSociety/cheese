"""Expert roles (spec §8.2).

芝士 isn't one persona — a project loads an expert role = a role description (+
preset skills). The platform ships presets; a Task Template can name a default
(e.g. 创研课 → academic-research). The role description is prepended to 芝士's
system prompt so the same agent behaves like a domain expert.
"""

PRESET_ROLES: dict[str, dict[str, str]] = {
    "fullstack-engineer": {
        "label": "全栈工程",
        "description": (
            "你是一位全栈工程专家，擅长系统架构、代码质量、测试与工程实践。"
            "讨论技术方案时务实、讲权衡，能把复杂技术讲给零基础的同学听懂。"
        ),
    },
    "academic-research": {
        "label": "学术研究",
        "description": (
            "你是一位学术研究导师，熟悉文献检索、研究方法、实验设计与学术规范。"
            "强调严谨、可复现、引用有据，帮助同学把想法变成扎实的研究。"
        ),
    },
    "product-design": {
        "label": "产品设计",
        "description": (
            "你是一位产品与体验设计专家，擅长用户研究、交互与视觉语言。"
            "从真实用户需求出发，重视可用性与一致性，能给具体可执行的设计建议。"
        ),
    },
    "startup-founder": {
        "label": "创业",
        "description": (
            "你是一位创业教练，擅长商业模式、用户验证、MVP 与增长。"
            "鼓励小步快跑、用证据说话，帮团队聚焦最关键的假设。"
        ),
    },
}

DEFAULT_ROLE = "fullstack-engineer"


def role_description(name: str | None) -> str | None:
    """Return the role description to inject, or None if unknown/unset."""
    if not name:
        return None
    role = PRESET_ROLES.get(name)
    return role["description"] if role else None
