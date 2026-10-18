"""Serve Alibaba Bailian's Qwen-Audio ASR models as a chat model.

Qwen-Audio-3.x-ASR-Flash is reachable only through DashScope's native
multimodal-generation API, and LiteLLM's /audio/transcriptions route has no
DashScope provider. This handler is registered through LiteLLM's public
``custom_provider_map``, so callers keep the one shape every other model has:
a chat completion whose last user message carries the audio. Keys, prices and
the spend log stay in the gateway like everywhere else.

The request a caller sends:

- the last user message holds one ``input_audio`` part, its ``data`` a
  ``data:audio/<format>;base64,...`` URL;
- a system message, when present, is context the recogniser reads before the
  audio (names, the conversation so far); it is sent as DashScope context;
- ``vocabulary``, when present, is DashScope's instant hotword table, a mapping
  from word to weight (1-5, or 50 for a super hotword).

The reply's content is the transcript. DashScope bills by audio length and
reports it as ``usage.duration`` in seconds; the handler reports it as prompt
tokens at DashScope's own rate of 25 tokens a second, a part second counted
whole, so the row's ``input_cost_per_token`` prices it.
"""

import math
import re

import httpx
from litellm import CustomLLM
from litellm.llms.custom_llm import CustomLLMError
from litellm.types.utils import ModelResponse, Usage

TOKENS_PER_SECOND = 25
_PATH = "/api/v1/services/aigc/multimodal-generation/generation"
_DATA_URL = re.compile(r"^data:audio/([\w.+-]+);base64,", re.IGNORECASE)
# MIME subtypes whose DashScope format name differs.
_FORMATS = {"mpeg": "mp3", "x-wav": "wav", "wave": "wav", "ogg": "opus"}


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(
        part.get("text") or ""
        for part in content or []
        if isinstance(part, dict) and part.get("type") == "text"
    )


def _audio(messages: list) -> str:
    for message in reversed(messages):
        if message.get("role") != "user" or isinstance(message.get("content"), str):
            continue
        for part in message.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "input_audio":
                data = (part.get("input_audio") or {}).get("data")
                if isinstance(data, str) and _DATA_URL.match(data):
                    return data
        break
    raise CustomLLMError(
        status_code=400,
        message="the last user message needs an input_audio part with a data:audio/...;base64 URL",
    )


def upstream_body(model: str, messages: list, optional_params: dict) -> dict:
    """The DashScope request for one chat-shaped ASR call."""
    audio = _audio(messages)
    subtype = _DATA_URL.match(audio).group(1).lower()
    context = "\n".join(
        text for m in messages if m.get("role") == "system" and (text := _text(m.get("content")).strip())
    )
    turns = []
    if context:
        turns.append({"role": "user", "content": [{"type": "input_text", "text": context}]})
    turns.append({"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": audio}}]})
    parameters: dict = {"format": _FORMATS.get(subtype, subtype)}
    extra = {**(optional_params.get("extra_body") or {}), **optional_params}
    if vocabulary := extra.get("vocabulary"):
        parameters["vocabulary"] = vocabulary
    if hints := extra.get("language_hints"):
        parameters["language_hints"] = hints
    return {"model": model, "input": {"messages": turns}, "parameters": parameters}


class QwenASR(CustomLLM):
    async def acompletion(
        self,
        model,
        messages,
        api_base,
        custom_prompt_dict,
        model_response: ModelResponse,
        print_verbose,
        encoding,
        api_key,
        logging_obj,
        optional_params,
        acompletion=None,
        litellm_params=None,
        logger_fn=None,
        headers={},
        timeout=None,
        client=None,
    ) -> ModelResponse:
        body = upstream_body(model, messages, optional_params)
        async with httpx.AsyncClient(timeout=timeout or 60) as http:
            r = await http.post(
                f"{api_base.rstrip('/')}{_PATH}",
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
            )
        if r.status_code != 200:
            raise CustomLLMError(status_code=r.status_code, message=r.text[:500])
        payload = r.json()
        output = payload.get("output") or {}
        text = output.get("text")
        if text is None:
            choices = output.get("choices") or [{}]
            text = _text((choices[0].get("message") or {}).get("content"))
        seconds = float((payload.get("usage") or {}).get("duration") or 0)
        tokens = math.ceil(seconds) * TOKENS_PER_SECOND
        model_response.model = model
        model_response.choices[0].message.content = text or ""
        model_response.choices[0].finish_reason = "stop"
        setattr(model_response, "usage", Usage(prompt_tokens=tokens, completion_tokens=0, total_tokens=tokens))
        return model_response

    def completion(self, *args, **kwargs):
        raise CustomLLMError(status_code=400, message="qwen-asr serves async requests only")


handler = QwenASR()
