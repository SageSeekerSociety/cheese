"""Gemini 4 gets the Gemini 3+ handling; Gemini 1.x/2.x and Gemma do not."""

from litellm.litellm_core_utils.prompt_templates.factory import (
    convert_to_gemini_tool_call_invoke,
)
from litellm.llms.vertex_ai.gemini.vertex_and_google_ai_studio_gemini import (
    VertexGeminiConfig,
)

for model in (
    "gemini-4-argon",
    "gemini/gemini-4-argon",
    "gemini-3.8-flash",
    "gemini-flash-latest",
):
    assert VertexGeminiConfig._is_gemini_3_or_newer(model), model
for model in (
    "gemini-2.5-pro",
    "gemini/gemini-2.5-flash",
    "gemini-1.5-pro",
    "gemini-pro",
    "gemini-flash",
    "gemma-4-31b-it",
):
    assert not VertexGeminiConfig._is_gemini_3_or_newer(model), model

# A tool call carrying no signature (history written by another model) is sent
# with the documented placeholder, which Gemini 3+ requires on the first call.
parts = convert_to_gemini_tool_call_invoke(
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "toolu_1",
                "type": "function",
                "function": {"name": "Read", "arguments": '{"file_path": "/x"}'},
            }
        ],
    },
    model="gemini-4-argon",
)
assert parts[0].get("thoughtSignature"), parts
print("gemini generation ok")
