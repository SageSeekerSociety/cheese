"""Whatever a person's 芝士 can do on the person's behalf, it is handed the
tool to ask for: each action the sandbox carries out as the person — sending,
opening a task, managing people, running a space — is a tool, or a verb of
one, among those the person's session is given. An action left off that list
is one the model never sees, however well the backend would have done it."""

from importlib.machinery import SourceFileLoader
from pathlib import Path

from app.domain.agent.harness.pi import catalog
from app.domain.agent.personal.session import TOOLS

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"
cheese = SourceFileLoader("cheese_cli_handed", str(_CHEESE)).load_module()


def test_every_action_taken_as_the_person_is_a_tool_it_is_handed():
    handed = {s["name"]: s for s in catalog.schemas_of(list(TOOLS))}

    missing = []
    for step in cheese._DELEGATED_STEPS:
        tool, _, verb = step.partition(".")
        schema = handed.get(tool)
        if schema is None:
            missing.append(step)
            continue
        if verb:
            verbs = schema["inputSchema"]["properties"]["action"]["enum"]
            if verb not in verbs:
                missing.append(step)

    assert missing == []
