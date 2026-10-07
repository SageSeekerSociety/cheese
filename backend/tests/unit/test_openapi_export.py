"""The pieces of the OpenAPI export that are pure: what ``typed_response`` writes
and what counts as untyped. Whether the committed document is current is
``scripts.export_openapi --check`` in the backend lint job — building the whole
document does not fit this layer's per-test ceiling.
"""

from app.api.response import Deleted, ok, typed_response
from scripts import export_openapi


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
