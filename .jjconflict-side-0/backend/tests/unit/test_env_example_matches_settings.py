"""Every key in .env.example must be a real Settings field.

``Settings`` sets ``extra="ignore"``, so an env var matching no field is dropped
without a word. That makes a stale template worse than no template: the reader
sets the value, the app never reads it, and nothing anywhere says so. The
template shipped ``ANTHROPIC_API_KEY`` for months — no field has ever read it,
so every local setup that "configured the model" that way configured nothing.

This is a lint on documentation, not on behaviour, which is exactly why it has
to be automated: nobody re-reads a template against a 600-line settings class.
"""

from pathlib import Path

from app.core.config import Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def _template_keys() -> list[str]:
    keys: list[str] = []
    for raw in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.append(line.split("=", 1)[0].strip())
    return keys


def _accepted_env_names() -> set[str]:
    """Env names Settings actually reads: the field name, plus its alias when a
    field declares one (several do — the env var and the attribute differ)."""
    names: set[str] = set()
    for name, field in Settings.model_fields.items():
        names.add(name.upper())
        if field.alias:
            names.add(field.alias.upper())
        if field.validation_alias and isinstance(field.validation_alias, str):
            names.add(field.validation_alias.upper())
    return names


def test_env_example_has_keys() -> None:
    # A template that parsed to nothing would make the check below vacuous.
    assert len(_template_keys()) >= 10


def test_every_env_example_key_is_a_settings_field() -> None:
    accepted = _accepted_env_names()
    unknown = [key for key in _template_keys() if key.upper() not in accepted]
    assert not unknown, (
        f"{ENV_EXAMPLE.name} advertises keys no Settings field reads: {unknown}. "
        "Settings ignores unknown env vars, so these configure nothing — either "
        "add the field or drop the line."
    )


def test_no_duplicate_keys() -> None:
    keys = _template_keys()
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    assert not duplicates, f"{ENV_EXAMPLE.name} sets these twice: {duplicates}"
