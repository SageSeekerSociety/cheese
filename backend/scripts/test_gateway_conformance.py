"""The conformance suite must fail the breakages that reached production, and
only those: canned gateway responses, no network."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import gateway_conformance as suite  # noqa: E402
import gateway_supply_probe as probe  # noqa: E402


def sse(events, done=False):
    body = b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)
    return body + (b"data: [DONE]\n\n" if done else b"")


def messages(*blocks, stop="end_turn", usage=None, terminal=True):
    """A Messages stream carrying ``blocks``: ("text", str), ("thinking", str)
    or ("tool", name, raw_json_arguments)."""
    events = [
        {
            "type": "message_start",
            "message": {"usage": usage or {"input_tokens": 50, "output_tokens": 1}},
        }
    ]
    for i, block in enumerate(blocks):
        if block[0] == "tool":
            start = {"type": "tool_use", "id": f"t{i}", "name": block[1], "input": {}}
            delta = {"type": "input_json_delta", "partial_json": block[2]}
        elif block[0] == "thinking":
            start = {"type": "thinking", "thinking": ""}
            delta = {"type": "thinking_delta", "thinking": block[1]}
        else:
            start = {"type": "text", "text": ""}
            delta = {"type": "text_delta", "text": block[1]}
        events += [
            {"type": "content_block_start", "index": i, "content_block": start},
            {"type": "content_block_delta", "index": i, "delta": delta},
            {"type": "content_block_stop", "index": i},
        ]
    events.append(
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop},
            "usage": {"output_tokens": 7},
        }
    )
    if terminal:
        events.append({"type": "message_stop"})
    return sse(events)


def chat(text="", calls=(), finish="stop"):
    events = []
    if text:
        events.append({"choices": [{"index": 0, "delta": {"content": text}}]})
    for i, (name, args) in enumerate(calls):
        events.append(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": i,
                                    "id": f"c{i}",
                                    "function": {"name": name, "arguments": args},
                                }
                            ]
                        },
                    }
                ]
            }
        )
    events.append({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
    events.append(
        {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 2}}
    )
    return sse(events, done=True)


class FakeSession(suite.Session):
    """A Session whose gateway answers from a script, through the real parsers."""

    def __init__(self, replies, **info):
        super().__init__("http://gateway", "key", suite.Model("m", info), 4096)
        self.replies, self.sent = list(replies), []

    def _next(self, payload):
        self.sent.append(payload)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply.splitlines(keepends=True)

    def messages(self, payload):
        return suite.parse_messages_stream(self._next(payload))

    def chat(self, payload):
        return suite.parse_chat_stream(self._next(payload))


def verdict(check, replies, **info):
    session = FakeSession(replies, **info)
    [result] = suite.run(session, [check])
    return result.status, result.reason, session


class StreamTests(unittest.TestCase):
    def test_answer_with_usage_passes(self):
        status, _, _ = verdict("stream_text", [messages(("text", "pineapple"))])
        self.assertEqual(status, "pass")

    def test_stream_ending_without_its_terminal_event_fails(self):
        # #3570: an upstream failure that ends the stream silently.
        status, reason, _ = verdict(
            "stream_text", [messages(("text", "pine"), terminal=False)]
        )
        self.assertEqual(status, "fail")
        self.assertIn("stream_incomplete", reason)

    def test_error_event_mid_stream_fails_with_its_message(self):
        body = sse(
            [
                {"type": "message_start", "message": {"usage": {}}},
                {
                    "type": "error",
                    "error": {"type": "overloaded_error", "message": "upstream down"},
                },
            ]
        )
        status, reason, _ = verdict("stream_text", [body])
        self.assertEqual(status, "fail")
        self.assertIn("upstream down", reason)

    def test_zero_usage_fails(self):
        status, reason, _ = verdict(
            "stream_text",
            [
                messages(
                    ("text", "pineapple"), usage={"input_tokens": 0, "output_tokens": 0}
                ).replace(b'"output_tokens": 7', b'"output_tokens": 0')
            ],
        )
        self.assertEqual(status, "fail")
        self.assertIn("usage", reason)

    def test_running_out_of_tokens_is_named(self):
        # #2786-style: a reasoning model spends the whole budget thinking.
        status, reason, _ = verdict(
            "stream_text", [messages(("thinking", "hmm"), stop="max_tokens")]
        )
        self.assertEqual(status, "fail")
        self.assertIn("max_tokens", reason)

    def test_rejected_request_reports_status_and_provider_message(self):
        # #2099/#2244/#2917: a body field the upstream refuses.
        refused = probe.ProbeFailure(
            "http_error", 400, detail="Unknown field: context_management"
        )
        status, reason, _ = verdict("stream_text", [refused])
        self.assertEqual(status, "fail")
        self.assertIn("HTTP 400", reason)
        self.assertIn("context_management", reason)


class ToolTests(unittest.TestCase):
    def test_valid_tool_call_passes(self):
        status, _, _ = verdict(
            "tool_call",
            [
                messages(
                    ("tool", "Read", '{"file_path": "/etc/hostname"}'), stop="tool_use"
                )
            ],
        )
        self.assertEqual(status, "pass")

    def test_empty_tool_arguments_fail(self):
        status, reason, _ = verdict(
            "tool_call", [messages(("tool", "Read", ""), stop="tool_use")]
        )
        self.assertEqual(status, "fail")
        self.assertIn("empty", reason)

    def test_truncated_tool_arguments_fail(self):
        status, reason, _ = verdict(
            "tool_call",
            [messages(("tool", "Read", '{"file_path": "/et'), stop="tool_use")],
        )
        self.assertEqual(status, "fail")
        self.assertIn("not JSON", reason)

    def test_answering_in_text_instead_of_calling_fails(self):
        status, _, _ = verdict("tool_call", [messages(("text", "I can't read files"))])
        self.assertEqual(status, "fail")

    def test_three_parallel_calls_with_arguments_pass(self):
        calls = [("tool", "get_weather", json.dumps({"city": c})) for c in suite.CITIES]
        status, _, _ = verdict(
            "parallel_tool_calls", [messages(*calls, stop="tool_use")]
        )
        self.assertEqual(status, "pass")

    def test_one_parallel_call_losing_its_arguments_fails(self):
        # #3600: the later calls of a parallel batch arrive with empty input.
        calls = [
            ("tool", "get_weather", '{"city": "Paris"}'),
            ("tool", "get_weather", ""),
            ("tool", "get_weather", ""),
        ]
        status, reason, _ = verdict(
            "parallel_tool_calls", [messages(*calls, stop="tool_use")]
        )
        self.assertEqual(status, "fail")
        self.assertIn("call 2 of 3", reason)

    def test_a_single_call_does_not_exercise_the_parallel_path(self):
        status, _, _ = verdict(
            "parallel_tool_calls",
            [messages(("tool", "get_weather", '{"city": "Paris"}'), stop="tool_use")],
        )
        self.assertEqual(status, "warn")

    def test_answer_using_the_tool_result_passes(self):
        status, _, session = verdict(
            "tool_result_roundtrip", [messages(("text", "4817"))]
        )
        self.assertEqual(status, "pass")
        roles = [m["role"] for m in session.sent[0]["messages"]]
        self.assertEqual(roles, ["user", "assistant", "user"])

    def test_answer_ignoring_the_tool_result_fails(self):
        status, _, _ = verdict(
            "tool_result_roundtrip", [messages(("text", "I don't know"))]
        )
        self.assertEqual(status, "fail")


class ImageTests(unittest.TestCase):
    def test_skipped_without_declared_vision(self):
        status, _, session = verdict("image_in_tool_result", [])
        self.assertEqual(status, "skip")
        self.assertEqual(session.sent, [])

    def answer(self, text):
        with patch.object(suite.random, "choice", lambda words: "ZEBRA"):
            return verdict(
                "image_in_tool_result", [messages(("text", text))], supports_vision=True
            )

    def test_naming_the_word_in_the_image_passes(self):
        status, _, session = self.answer("Zebra")
        self.assertEqual(status, "pass")
        result = session.sent[0]["messages"][-1]["content"][0]
        self.assertEqual(result["type"], "tool_result")
        self.assertEqual(result["content"][0]["type"], "image")

    def test_answer_without_the_word_fails(self):
        # #1358: the image inside the tool result never reached the model.
        status, _, _ = self.answer("I don't see any image")
        self.assertEqual(status, "fail")

    def test_every_word_renders_as_a_png(self):
        for word in suite.WORDS:
            self.assertTrue(suite.word_png(word).startswith(b"\x89PNG\r\n\x1a\n"))


class ThinkingTests(unittest.TestCase):
    def test_skipped_without_declared_efforts(self):
        status, _, _ = verdict("thinking_effort", [])
        self.assertEqual(status, "skip")

    def test_every_declared_effort_is_sent_and_thinks(self):
        status, _, session = verdict(
            "thinking_effort",
            [messages(("thinking", "17*23"), ("text", "391"))] * 2,
            cheese_efforts=["low", "high"],
            supports_reasoning=True,
        )
        self.assertEqual(status, "pass")
        self.assertEqual(
            [p["output_config"]["effort"] for p in session.sent], ["low", "high"]
        )

    def test_rejected_effort_fails(self):
        refused = probe.ProbeFailure(
            "http_error", 400, detail="effort max not supported"
        )
        status, reason, _ = verdict(
            "thinking_effort",
            [messages(("text", "391")), refused],
            cheese_efforts=["low", "max"],
        )
        self.assertEqual(status, "fail")
        self.assertIn("max: http_error HTTP 400", reason)

    def test_reasoning_model_without_thinking_fails(self):
        status, reason, _ = verdict(
            "thinking_effort",
            [messages(("text", "391"))],
            cheese_efforts=["high"],
            supports_reasoning=True,
        )
        self.assertEqual(status, "fail")
        self.assertIn("no thinking", reason)


class MidConversationSystemTests(unittest.TestCase):
    def run_check(self, text, **info):
        with (
            patch.object(suite.random, "choice", lambda words: "marzipan"),
            patch.object(suite.random, "randrange", lambda a, b=None: 512),
        ):
            return verdict(
                "mid_conversation_system", [messages(("text", text))], **info
            )

    def test_honoured_passes_and_the_system_message_is_mid_conversation(self):
        status, _, session = self.run_check("marzipan-512")
        self.assertEqual(status, "pass")
        self.assertEqual(session.sent[0]["messages"][-1]["role"], "system")

    def test_dropped_on_an_unmarked_model_fails(self):
        status, _, _ = self.run_check("I don't see a passphrase")
        self.assertEqual(status, "fail")

    def test_dropped_on_a_marked_model_passes(self):
        status, _, _ = self.run_check("no idea", supports_mid_conversation_system=False)
        self.assertEqual(status, "pass")

    def test_honoured_on_a_marked_model_warns_the_mark_can_go(self):
        status, _, _ = self.run_check(
            "marzipan-512", supports_mid_conversation_system=False
        )
        self.assertEqual(status, "warn")


class CacheTests(unittest.TestCase):
    def pair(self, read, **info):
        cold = messages(
            ("text", "19"),
            usage={"input_tokens": 6000, "cache_creation_input_tokens": 0},
        )
        warm = messages(
            ("text", "19"),
            usage={"input_tokens": 6000 - read, "cache_read_input_tokens": read},
        )
        return verdict("cache_hit", [cold, warm], **info)

    def test_second_request_reading_most_of_the_prompt_passes(self):
        status, _, session = self.pair(5900, cache_read_input_token_cost=1e-8)
        self.assertEqual(status, "pass")
        first, second = session.sent
        self.assertEqual(first["system"][1:], second["system"][1:])

    def test_cache_miss_on_a_priced_cache_fails(self):
        # #3375/#2635: the prefix stopped hitting.
        status, reason, _ = self.pair(20, cache_read_input_token_cost=1e-8)
        self.assertEqual(status, "fail")
        self.assertIn("20 of 6000", reason)

    def test_cache_miss_without_a_cache_price_warns(self):
        status, _, _ = self.pair(0)
        self.assertEqual(status, "warn")


class ChatCompletionsTests(unittest.TestCase):
    def test_text_and_tool_call_pass_with_pi_compat_shape(self):
        status, _, session = verdict(
            "chat_completions",
            [
                chat("pineapple"),
                chat(
                    calls=[("read", '{"file_path": "/etc/hostname"}')],
                    finish="tool_calls",
                ),
            ],
        )
        self.assertEqual(status, "pass")
        for payload in session.sent:
            self.assertIn("max_tokens", payload)
            self.assertNotIn("max_completion_tokens", payload)
            self.assertNotIn("store", payload)
            self.assertNotIn("developer", [m["role"] for m in payload["messages"]])

    def test_tool_call_without_arguments_fails(self):
        status, reason, _ = verdict(
            "chat_completions",
            [chat("pineapple"), chat(calls=[("read", "")], finish="tool_calls")],
        )
        self.assertEqual(status, "fail")
        self.assertIn("empty", reason)

    def test_stream_without_finish_reason_fails(self):
        cut = sse([{"choices": [{"index": 0, "delta": {"content": "pine"}}]}])
        status, reason, _ = verdict("chat_completions", [cut])
        self.assertEqual(status, "fail")
        self.assertIn("stream_incomplete", reason)


class Response(io.BytesIO):
    def __init__(self, body, sse_body=False):
        super().__init__(body)
        self.headers = {
            "Content-Type": "text/event-stream" if sse_body else "application/json"
        }


class SuiteRunTests(unittest.TestCase):
    CATALOG = {
        "data": [
            {
                "model_name": "menu-model",
                "model_info": {"id": "d1", "cheese_selectable": True},
            },
            {
                "model_name": "menu-model",
                "model_info": {"id": "d2", "cheese_selectable": True},
            },
            {"model_name": "hidden-model", "model_info": {"id": "d3"}},
        ]
    }

    def main(self, args, answer):
        requests, out, err = [], io.StringIO(), io.StringIO()

        def urlopen(request, timeout):
            requests.append(request)
            if request.full_url.endswith("/model/info"):
                return Response(json.dumps(self.CATALOG).encode())
            return answer(request)

        with (
            patch.dict(os.environ, {"GATEWAY_KEY": "secret-key"}),
            patch.object(probe.urllib.request, "urlopen", urlopen),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = suite.main(["--base", "http://gateway", *args])
        self.assertNotIn("secret-key", out.getvalue() + err.getvalue())
        return code, out.getvalue(), requests

    def test_default_checks_each_selectable_model_once_and_exits_zero_on_pass(self):
        code, out, requests = self.main(
            ["--checks", "stream_text"],
            lambda request: Response(messages(("text", "pineapple")), True),
        )
        self.assertEqual(code, 0)
        sent = [json.loads(r.data)["model"] for r in requests if r.data]
        self.assertEqual(sent, ["menu-model"])
        self.assertIn("pass", out)

    def test_claude_code_headers_are_sent(self):
        _, _, requests = self.main(
            ["--checks", "stream_text"],
            lambda request: Response(messages(("text", "pineapple")), True),
        )
        headers = {k.lower(): v for k, v in requests[-1].header_items()}
        self.assertIn("claude-code-20250219", headers["anthropic-beta"])
        self.assertIn("x-claude-code-session-id", headers)

    def test_any_failure_makes_the_exit_code_nonzero_and_lands_in_json(self):
        def refuse(request):
            raise urllib.error.HTTPError(
                request.full_url,
                400,
                "bad",
                {},
                io.BytesIO(b'{"error":{"message":"no"}}'),
            )

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "out.json"
            code, _, _ = self.main(
                ["--checks", "stream_text,tool_call", "--json", str(path)], refuse
            )
            report = json.loads(path.read_text())
        self.assertEqual(code, 1)
        self.assertEqual(
            [(r["check"], r["status"]) for r in report["results"]],
            [("stream_text", "fail"), ("tool_call", "fail")],
        )

    def test_pinning_a_deployment_sends_its_id(self):
        _, out, requests = self.main(
            ["--deployment", "d2", "--checks", "stream_text"],
            lambda request: Response(messages(("text", "pineapple")), True),
        )
        self.assertEqual(json.loads(requests[-1].data)["model"], "d2")
        self.assertIn("menu-model@d2", out)

    def test_unknown_check_is_a_usage_error(self):
        with self.assertRaises(SystemExit):
            self.main(["--checks", "nope"], lambda request: None)


if __name__ == "__main__":
    unittest.main()
