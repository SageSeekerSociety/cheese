"""The pieces of the OpenAPI export that are pure: what ``typed_response`` writes,
what counts as untyped, and what each flag writes and judges. Whether the tree's
ratchet is current is ``scripts.export_openapi --check`` in the backend lint job —
building the whole document does not fit this layer's per-test ceiling, so every
case here stubs ``build_spec`` with a spec it can reason about.
"""

import pytest

from app.api.response import Deleted, ok, typed_response
from scripts import export_openapi


def _untyped_spec() -> dict:
    """One untyped GET, enough to exercise every branch of ``main``."""
    # A success response with an ``application/json`` schema that is empty is
    # what ``untyped_operations`` counts — see ``_success_schema``.
    response = {"content": {"application/json": {"schema": {}}}}
    return {
        "openapi": "3.1.0",
        "paths": {
            "/a": {"get": {"operationId": "a", "responses": {"200": response}}},
        },
    }


def _wire(monkeypatch, tmp_path) -> tuple:
    spec = tmp_path / "openapi.json"
    untyped = tmp_path / "openapi-untyped.json"
    monkeypatch.setattr(export_openapi, "SPEC_PATH", spec)
    monkeypatch.setattr(export_openapi, "UNTYPED_PATH", untyped)
    monkeypatch.setattr(export_openapi, "build_spec", _untyped_spec)
    return spec, untyped


def test_typed_response_documents_the_envelope_without_validating() -> None:
    kwargs = typed_response(Deleted)
    assert kwargs["response_model"] is None
    model = kwargs["responses"][200]["model"]
    assert model.__name__ == "DeletedEnvelope"
    assert typed_response(Deleted)["responses"][200]["model"] is model
    # The wire format the model describes is the one ok() builds.
    assert model.model_validate(ok({"deleted": True})).data.deleted is True


def test_untyped_means_no_schema() -> None:
    def op(schema: dict | None) -> dict:
        content = {} if schema is None else {"application/json": {"schema": schema}}
        return {"responses": {"200": {"content": content}}}

    spec = {
        "paths": {
            "/a": {"get": op({"type": "object", "additionalProperties": True})},
            "/b": {"get": op({})},
            "/c": {"get": op({"$ref": "#/components/schemas/X"})},
            "/d": {"get": op(None)},
        }
    }
    assert export_openapi.untyped_operations(spec) == ["GET /a", "GET /b"]


def test_operation_order_survives_the_order_fastapi_happened_to_build() -> None:
    """A route's methods arrive as a set, so their order must not reach the file.

    ``--check`` compares the committed document as text against a fresh build, and
    a set iterates in hash-seed order: without this, unchanged code fails the check
    on some runs and passes on others.
    """

    def spec(*methods: str) -> dict:
        item = {method: {"operationId": method} for method in methods}
        return {"paths": {"/c": item}, "info": {"title": "t"}}

    assert export_openapi.render(export_openapi.canonicalize(spec("head", "get"))) == (
        export_openapi.render(export_openapi.canonicalize(spec("get", "head")))
    )


def test_check_and_schema_cannot_be_asked_for_together(capsys) -> None:
    """A run that both wrote the document and skipped the ratchet would be a way
    to generate the types while the ratchet went unwatched, so the two are
    mutually exclusive: the frontend's generator passes ``--schema`` and the
    backend lint job passes ``--check``, and neither can name both."""
    with pytest.raises(SystemExit) as exit:
        export_openapi.main(["--check", "--schema"])
    assert exit.value.code == 2
    # The pair, not an unknown flag: argparse names what it refused to combine.
    assert "not allowed with argument --check" in capsys.readouterr().err


def test_schema_writes_the_document_and_not_the_ratchet(monkeypatch, tmp_path) -> None:
    """The consumer path. The document is what the frontend reads, so it is
    written; the ratchet is committed and the backend lint job owns it, so a
    generator that only needs the document must not move it."""
    spec, untyped = _wire(monkeypatch, tmp_path)
    untyped.write_text("sentinel\n", encoding="utf-8")
    assert export_openapi.main(["--schema"]) == 0
    assert spec.is_file()
    assert untyped.read_text(encoding="utf-8") == "sentinel\n"


def test_check_regenerates_the_document_and_only_judges_the_ratchet(
    monkeypatch, tmp_path, capsys
) -> None:
    """``--check`` still leaves the document behind — a reader needs it — but its
    verdict is the ratchet alone, which is what the lint job gates on."""
    spec, untyped = _wire(monkeypatch, tmp_path)
    untyped.unlink(missing_ok=True)
    assert export_openapi.main(["--check"]) == 1  # no ratchet yet: stale
    assert spec.is_file()
    assert "stale" in capsys.readouterr().err
    untyped.write_text(
        export_openapi.render_untyped(
            export_openapi.untyped_operations(_untyped_spec())
        ),
        encoding="utf-8",
    )
    spec.unlink()
    assert export_openapi.main(["--check"]) == 0
    assert spec.is_file()


def test_check_reports_a_new_untyped_operation_without_rebaselining(
    monkeypatch, tmp_path, capsys
) -> None:
    """A new untyped response fails the check, and the committed ratchet is left
    at its old contents — quietly widening it is the failure mode it guards."""
    spec, untyped = _wire(monkeypatch, tmp_path)
    empty = export_openapi.render_untyped([])
    untyped.write_text(empty, encoding="utf-8")
    assert export_openapi.main(["--check"]) == 1
    assert "untyped responses on new operations" in capsys.readouterr().err
    assert untyped.read_text(encoding="utf-8") == empty
    assert spec.is_file()  # the document is still generated for readers
