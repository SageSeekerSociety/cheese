from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/dependency-audit.yml"
REPAIR = (
    "修法：在 .github/workflows/dependency-audit.yml 的 audit job 中，"
    "为 astral-sh/setup-uv 的 with 设置 enable-cache: true 和 "
    "cache-dependency-glob: backend/uv.lock。复查：cd backend && uv run pytest "
    "tests/unit/test_dependency_audit_workflow_cache.py -q"
)


def _assert_audit_cache(workflow_yaml: str) -> None:
    workflow = yaml.safe_load(workflow_yaml)
    setup_steps = [
        step
        for step in workflow["jobs"]["audit"]["steps"]
        if step.get("uses", "").startswith("astral-sh/setup-uv@")
    ]
    assert len(setup_steps) == 1, "audit job 必须有一个 setup-uv 步骤。\n" + REPAIR
    inputs = setup_steps[0].get("with") or {}
    enabled = inputs.get("enable-cache")
    assert enabled is True or enabled == "true", (
        "setup-uv 必须显式启用 enable-cache: true。\n" + REPAIR
    )
    assert inputs.get("cache-dependency-glob") == "backend/uv.lock", (
        "setup-uv 的 cache-dependency-glob 必须指向 backend/uv.lock。\n" + REPAIR
    )


def test_dependency_audit_caches_uv_against_backend_lock():
    _assert_audit_cache(WORKFLOW.read_text(encoding="utf-8"))


def _workflow_fragment(inputs: str) -> str:
    return (
        "jobs:\n"
        "  audit:\n"
        "    steps:\n"
        "      - uses: astral-sh/setup-uv@fixture\n"
        "        with:\n"
        + "".join(f"          {line}\n" for line in inputs.splitlines())
    )


@pytest.mark.parametrize("enabled", ["true", '"true"'])
def test_cache_guard_accepts_yaml_and_action_string_booleans(enabled):
    _assert_audit_cache(
        _workflow_fragment(
            f"enable-cache: {enabled}\ncache-dependency-glob: backend/uv.lock"
        )
    )


@pytest.mark.parametrize(
    ("inputs", "broken_input"),
    [
        ("cache-dependency-glob: backend/uv.lock", "enable-cache"),
        ("enable-cache: false\ncache-dependency-glob: backend/uv.lock", "enable-cache"),
        (
            'enable-cache: "false"\ncache-dependency-glob: backend/uv.lock',
            "enable-cache",
        ),
        ("enable-cache: 1\ncache-dependency-glob: backend/uv.lock", "enable-cache"),
        ("enable-cache: true", "cache-dependency-glob"),
        ("enable-cache: true\ncache-dependency-glob: uv.lock", "cache-dependency-glob"),
    ],
)
def test_cache_guard_rejects_violations_and_explains_repair(inputs, broken_input):
    with pytest.raises(AssertionError) as caught:
        _assert_audit_cache(_workflow_fragment(inputs))
    message = str(caught.value)
    assert broken_input in message
    assert "修法" in message
    assert "enable-cache: true" in message
    assert "cache-dependency-glob: backend/uv.lock" in message
    assert "test_dependency_audit_workflow_cache.py -q" in message


def test_cache_guard_rejects_a_missing_setup_step():
    with pytest.raises(AssertionError, match="setup-uv.*步骤") as caught:
        _assert_audit_cache("jobs:\n  audit:\n    steps: []\n")
    assert REPAIR in str(caught.value)
