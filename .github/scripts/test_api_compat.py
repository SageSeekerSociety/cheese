"""What api_compat.py calls breaking for a page built against the old API, and
that a breaking change passes only with the generation raised."""

import contextlib
import copy
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location("api_compat", Path(__file__).with_name("api_compat.py"))
api_compat = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api_compat)


def _document():
    """A small document in FastAPI's shape: a body model, a response envelope, refs."""
    return {
        "paths": {
            "/topics/{topic_id}/messages": {
                "post": {
                    "parameters": [
                        {"in": "path", "name": "topic_id", "required": True, "schema": {"type": "string"}},
                        {"in": "query", "name": "notify", "required": False, "schema": {"type": "boolean"}},
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/PostIn"}}},
                    },
                    "responses": {
                        "200": {
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/PostOut"}}}
                        },
                        "422": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    },
                }
            },
            "/topics": {"get": {"responses": {"200": {"content": {"application/json": {"schema": {}}}}}}},
        },
        "components": {
            "schemas": {
                "PostIn": {
                    "type": "object",
                    "required": ["content"],
                    "properties": {
                        "content": {"type": "string"},
                        "reply_to": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                        "kind": {"type": "string", "enum": ["text", "image"]},
                    },
                },
                "PostOut": {
                    "type": "object",
                    "required": ["code", "data"],
                    "properties": {"code": {"type": "integer"}, "data": {"$ref": "#/components/schemas/Block"}},
                },
                "Block": {
                    "type": "object",
                    "required": ["id", "state", "children"],
                    "properties": {
                        "id": {"type": "string"},
                        "state": {"type": "string", "enum": ["queued", "done"]},
                        "children": {"type": "array", "items": {"$ref": "#/components/schemas/Block"}},
                    },
                },
            }
        },
    }


def _changed(edit):
    head = copy.deepcopy(_document())
    edit(head)
    return api_compat.breaking_changes(_document(), head)


def _schemas(doc):
    return doc["components"]["schemas"]


def _post(doc):
    return doc["paths"]["/topics/{topic_id}/messages"]["post"]


class BreakingForAnOldPage(unittest.TestCase):
    def assertBreaks(self, edit):
        self.assertNotEqual(_changed(edit), [])

    def assertDoesNotBreak(self, edit):
        self.assertEqual(_changed(edit), [])

    def test_the_same_document_breaks_nothing(self):
        self.assertDoesNotBreak(lambda doc: None)

    def test_a_removed_operation_breaks(self):
        self.assertBreaks(lambda doc: doc["paths"].pop("/topics"))
        self.assertBreaks(lambda doc: doc["paths"]["/topics/{topic_id}/messages"].pop("post"))

    def test_a_removed_or_renamed_response_field_breaks(self):
        def rename(doc):
            props = _schemas(doc)["Block"]["properties"]
            props["block_id"] = props.pop("id")

        self.assertBreaks(rename)

    def test_a_response_field_that_may_now_be_null_or_absent_breaks(self):
        self.assertBreaks(
            lambda doc: _schemas(doc)["Block"]["properties"].update(
                id={"anyOf": [{"type": "string"}, {"type": "null"}]}
            )
        )
        self.assertBreaks(lambda doc: _schemas(doc)["Block"]["required"].remove("id"))

    def test_a_removed_retyped_or_narrowed_request_field_breaks(self):
        self.assertBreaks(lambda doc: _schemas(doc)["PostIn"]["properties"].pop("reply_to"))
        self.assertBreaks(lambda doc: _schemas(doc)["PostIn"]["properties"].update(content={"type": "integer"}))
        self.assertBreaks(lambda doc: _schemas(doc)["PostIn"]["properties"].update(reply_to={"type": "string"}))
        self.assertBreaks(lambda doc: _post(doc)["parameters"].pop(1))

    def test_a_new_required_request_field_or_parameter_breaks(self):
        def required_field(doc):
            _schemas(doc)["PostIn"]["properties"]["channel"] = {"type": "string"}
            _schemas(doc)["PostIn"]["required"].append("channel")

        self.assertBreaks(required_field)
        self.assertBreaks(lambda doc: _post(doc)["parameters"][1].update(required=True))
        self.assertBreaks(lambda doc: _schemas(doc)["PostIn"]["required"].append("kind"))

    def test_an_enum_value_removed_breaks_on_either_side(self):
        self.assertBreaks(lambda doc: _schemas(doc)["PostIn"]["properties"]["kind"].update(enum=["text"]))
        self.assertBreaks(lambda doc: _schemas(doc)["Block"]["properties"]["state"].update(enum=["done"]))

    def test_additions_do_not_break(self):
        def add(doc):
            doc["paths"]["/rooms"] = {"get": {"responses": {"200": {}}}}
            _schemas(doc)["PostIn"]["properties"]["channel"] = {"type": "string"}
            _schemas(doc)["Block"]["properties"]["title"] = {"type": "string"}
            _schemas(doc)["Block"]["required"].append("title")
            _schemas(doc)["PostIn"]["properties"]["kind"]["enum"].append("file")
            _schemas(doc)["PostIn"]["properties"]["content"] = {"anyOf": [{"type": "string"}, {"type": "null"}]}
            _post(doc)["parameters"].append(
                {"in": "query", "name": "silent", "required": False, "schema": {"type": "boolean"}}
            )

        self.assertDoesNotBreak(add)

    def test_a_required_field_inside_a_new_optional_object_does_not_break(self):
        def add(doc):
            _schemas(doc)["PostIn"]["properties"]["poll"] = {
                "type": "object",
                "required": ["question"],
                "properties": {"question": {"type": "string"}},
            }

        self.assertDoesNotBreak(add)

    def test_a_new_kind_in_a_request_union_does_not_break(self):
        def kinds(doc, *names):
            _schemas(doc)["PostIn"]["properties"]["quote"] = {
                "anyOf": [
                    {
                        "oneOf": [
                            {"type": "object", "required": [name], "properties": {name: {"type": "string"}}}
                            for name in names
                        ]
                    },
                    {"type": "null"},
                ]
            }

        base = _document()
        kinds(base, "block_id", "file")
        head = copy.deepcopy(base)
        kinds(head, "block_id", "file", "url")
        self.assertEqual(api_compat.breaking_changes(base, head), [])

    def test_a_new_required_field_on_a_kind_already_sent_breaks(self):
        def kinds(doc, extra_required):
            _schemas(doc)["PostIn"]["properties"]["quote"] = {
                "oneOf": [
                    {
                        "type": "object",
                        "required": ["kind", "block_id", *extra_required],
                        "properties": {
                            "kind": {"const": "block", "type": "string"},
                            "block_id": {"type": "string"},
                            "frame_id": {"type": "string"},
                        },
                    },
                    {
                        "type": "object",
                        "required": ["kind", "path"],
                        "properties": {"kind": {"const": "file", "type": "string"}, "path": {"type": "string"}},
                    },
                ]
            }

        base = _document()
        kinds(base, [])
        head = copy.deepcopy(base)
        kinds(head, ["frame_id"])
        self.assertNotEqual(api_compat.breaking_changes(base, head), [])

    def test_a_kind_removed_from_a_request_union_breaks(self):
        def kinds(doc, *names):
            _schemas(doc)["PostIn"]["properties"]["quote"] = {
                "oneOf": [
                    {"type": "object", "properties": {"kind": {"const": name}}, "required": ["kind"]}
                    for name in names
                ]
            }

        base = _document()
        kinds(base, "block", "file")
        head = copy.deepcopy(base)
        kinds(head, "block")
        self.assertNotEqual(api_compat.breaking_changes(base, head), [])

    def test_a_new_required_field_in_an_optional_object_already_sent_breaks(self):
        def nested(doc, *required):
            _schemas(doc)["PostIn"]["properties"]["poll"] = {
                "anyOf": [
                    {
                        "type": "object",
                        "required": list(required),
                        "properties": {"question": {"type": "string"}, "closes": {"type": "string"}},
                    },
                    {"type": "null"},
                ]
            }

        base = _document()
        nested(base, "question")
        head = copy.deepcopy(base)
        nested(head, "question", "closes")
        self.assertNotEqual(api_compat.breaking_changes(base, head), [])

    def test_renaming_a_model_alone_does_not_break(self):
        def rename(doc):
            _schemas(doc)["Message"] = _schemas(doc).pop("Block")
            text = json.dumps(doc).replace("#/components/schemas/Block", "#/components/schemas/Message")
            doc.clear()
            doc.update(json.loads(text))

        self.assertDoesNotBreak(rename)


class TheGenerationGate(unittest.TestCase):
    def _run(self, head, base_generation, head_generation):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for name, doc in (("base.json", _document()), ("head.json", head)):
                path = Path(directory, name)
                path.write_text(json.dumps(doc), encoding="utf-8")
                paths.append(str(path))
            with contextlib.redirect_stdout(io.StringIO()):
                return api_compat.main(["api_compat.py", *paths, str(base_generation), str(head_generation)])

    def test_a_breaking_change_fails_unless_the_generation_rose(self):
        head = _document()
        head["paths"].pop("/topics")
        self.assertEqual(self._run(head, 3, 3), 1)
        self.assertEqual(self._run(head, 3, 4), 0)

    def test_a_change_that_breaks_nothing_passes_with_the_generation_alone(self):
        self.assertEqual(self._run(_document(), 3, 3), 0)


if __name__ == "__main__":
    unittest.main()
