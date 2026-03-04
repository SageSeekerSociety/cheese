from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI, APITimeoutError, APIConnectionError, RateLimitError, APIStatusError

from app.core.config import settings
from app.domain.task.models import Task


class LLMError(Exception):
    """Base exception for LLM errors."""

    pass


class LLMTimeoutError(LLMError):
    """Raised when LLM request times out."""

    pass


class LLMConnectionError(LLMError):
    """Raised when connection to LLM fails."""

    pass


class LLMRateLimitError(LLMError):
    """Raised when rate limit is exceeded."""

    pass


class LLMAPIError(LLMError):
    """Raised when LLM API returns an error."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class GeneratedAdvice:
    topic_summary: dict[str, Any]
    knowledge_fields: list[dict[str, Any]]
    learning_paths: list[dict[str, Any]]
    methodology: list[dict[str, Any]]
    team_tips: list[dict[str, Any]]


@dataclass
class LLMResponse:
    content: str
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int


@dataclass
class StreamChunk:
    content: str
    is_final: bool
    total_tokens: int | None = None


class LLMClient:
    def __init__(self) -> None:
        api_key = settings.openai_api_key
        base_url = settings.openai_base_url
        if api_key:
            self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        else:
            self._client = None

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    async def get_completion(
        self,
        *,
        prompt: str,
        system_prompt: str = "",
        model_type: str | None = None,
        json_response: bool = False,
        timeout: float = 60.0,
    ) -> LLMResponse:
        if not self.is_configured:
            return self._placeholder_response(prompt)

        model = self._get_model(model_type)
        temperature = settings.openai_temperature
        max_tokens = settings.openai_max_tokens

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response_format = {"type": "json_object"} if json_response else {"type": "text"}

        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise LLMTimeoutError(f"LLM request timed out after {timeout}s") from exc
        except APITimeoutError as exc:
            raise LLMTimeoutError(f"OpenAI API timeout: {exc}") from exc
        except APIConnectionError as exc:
            raise LLMConnectionError(f"Failed to connect to OpenAI: {exc}") from exc
        except RateLimitError as exc:
            raise LLMRateLimitError(f"OpenAI rate limit exceeded: {exc}") from exc
        except APIStatusError as exc:
            raise LLMAPIError(f"OpenAI API error: {exc}", status_code=exc.status_code) from exc

        content = response.choices[0].message.content or ""
        usage = response.usage

        return LLMResponse(
            content=content,
            total_tokens=usage.total_tokens if usage else 0,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )

    async def get_completion_with_history(
        self,
        *,
        messages: list[dict[str, str]],
        model_type: str | None = None,
        json_response: bool = False,
        timeout: float = 60.0,
    ) -> LLMResponse:
        if not self.is_configured:
            return self._placeholder_response("")

        model = self._get_model(model_type)
        temperature = settings.openai_temperature
        max_tokens = settings.openai_max_tokens
        response_format = {"type": "json_object"} if json_response else {"type": "text"}

        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise LLMTimeoutError(f"LLM request timed out after {timeout}s") from exc
        except APITimeoutError as exc:
            raise LLMTimeoutError(f"OpenAI API timeout: {exc}") from exc
        except APIConnectionError as exc:
            raise LLMConnectionError(f"Failed to connect to OpenAI: {exc}") from exc
        except RateLimitError as exc:
            raise LLMRateLimitError(f"OpenAI rate limit exceeded: {exc}") from exc
        except APIStatusError as exc:
            raise LLMAPIError(f"OpenAI API error: {exc}", status_code=exc.status_code) from exc

        content = response.choices[0].message.content or ""
        usage = response.usage

        return LLMResponse(
            content=content,
            total_tokens=usage.total_tokens if usage else 0,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )

    async def stream_completion(
        self,
        *,
        prompt: str,
        system_prompt: str = "",
        model_type: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        if not self.is_configured:
            yield StreamChunk(content=self._placeholder_response(prompt).content, is_final=True)
            return

        model = self._get_model(model_type)
        temperature = settings.openai_temperature
        max_tokens = settings.openai_max_tokens

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            stream = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )

            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield StreamChunk(content=chunk.choices[0].delta.content, is_final=False)
                if chunk.usage:
                    yield StreamChunk(
                        content="",
                        is_final=True,
                        total_tokens=chunk.usage.total_tokens,
                    )
        except APITimeoutError as exc:
            raise LLMTimeoutError(f"OpenAI API timeout: {exc}") from exc
        except APIConnectionError as exc:
            raise LLMConnectionError(f"Failed to connect to OpenAI: {exc}") from exc
        except RateLimitError as exc:
            raise LLMRateLimitError(f"OpenAI rate limit exceeded: {exc}") from exc
        except APIStatusError as exc:
            raise LLMAPIError(f"OpenAI API error: {exc}", status_code=exc.status_code) from exc

    async def stream_completion_with_history(
        self,
        *,
        messages: list[dict[str, str]],
        model_type: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        if not self.is_configured:
            yield StreamChunk(content=self._placeholder_response("").content, is_final=True)
            return

        model = self._get_model(model_type)
        temperature = settings.openai_temperature
        max_tokens = settings.openai_max_tokens

        try:
            stream = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )

            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield StreamChunk(content=chunk.choices[0].delta.content, is_final=False)
                if chunk.usage:
                    yield StreamChunk(
                        content="",
                        is_final=True,
                        total_tokens=chunk.usage.total_tokens,
                    )
        except APITimeoutError as exc:
            raise LLMTimeoutError(f"OpenAI API timeout: {exc}") from exc
        except APIConnectionError as exc:
            raise LLMConnectionError(f"Failed to connect to OpenAI: {exc}") from exc
        except RateLimitError as exc:
            raise LLMRateLimitError(f"OpenAI rate limit exceeded: {exc}") from exc
        except APIStatusError as exc:
            raise LLMAPIError(f"OpenAI API error: {exc}", status_code=exc.status_code) from exc

    def _get_model(self, model_type: str | None) -> str:
        if model_type == "reasoning":
            return settings.openai_reasoning_model
        return settings.openai_default_model

    def _placeholder_response(self, prompt: str) -> LLMResponse:
        return LLMResponse(
            content="[LLM not configured - placeholder response]",
            total_tokens=0,
            prompt_tokens=0,
            completion_tokens=0,
        )

    async def generate_task_advice(self, task: Task) -> GeneratedAdvice:
        if not self.is_configured:
            return self._generate_placeholder_advice(task)

        system_prompt = """你是一位专业的学习顾问和项目导师。根据给定的任务信息，生成结构化的建议。

请以 JSON 格式返回，包含以下字段：
- topic_summary: {keyPoints: string[]} 任务关键点总结
- knowledge_fields: [{name: string, description: string}] 需要掌握的知识领域
- learning_paths: [{stage: string, description: string, resources?: [{name, type, url?}]}] 学习路径
- methodology: [{step: string, description: string}] 方法论建议
- team_tips: [{role: string, description: string}] 团队协作建议"""

        user_prompt = f"""任务名称：{task.name}
任务简介：{task.intro or ""}
任务详情：{task.description or ""}

请分析这个任务并给出结构化的学习与执行建议。"""

        try:
            response = await self.get_completion(
                prompt=user_prompt,
                system_prompt=system_prompt,
                json_response=True,
            )
            parsed = json.loads(response.content)
            return GeneratedAdvice(
                topic_summary=parsed.get("topic_summary", {"keyPoints": []}),
                knowledge_fields=parsed.get("knowledge_fields", []),
                learning_paths=parsed.get("learning_paths", []),
                methodology=parsed.get("methodology", []),
                team_tips=parsed.get("team_tips", []),
            )
        except (json.JSONDecodeError, KeyError):
            return self._generate_placeholder_advice(task)

    def _generate_placeholder_advice(self, task: Task) -> GeneratedAdvice:
        intro = (task.intro or task.description or "任务简介").strip()
        topic_summary = {
            "keyPoints": [
                f"任务：{task.name}",
                f"简介：{intro[:100]}",
            ]
        }
        knowledge_fields = [
            {
                "name": "需求理解",
                "description": "收集并整理需求、明确成功指标。",
            },
            {
                "name": "实现方案",
                "description": "拆解子任务，输出执行计划与验收标准。",
            },
        ]
        learning_paths = [
            {
                "stage": "阶段一",
                "description": "阅读背景资料、调研竞品",
                "resources": [
                    {
                        "name": "任务说明",
                        "type": "doc",
                        "url": None,
                    }
                ],
            },
            {
                "stage": "阶段二",
                "description": "制作原型或 PoC 并收集反馈",
            },
        ]
        methodology = [
            {"step": "调研", "description": "访谈潜在用户"},
            {"step": "方案", "description": "列出多种方案并评估"},
            {"step": "交付", "description": "形成汇报材料"},
        ]
        team_tips = [
            {"role": "Owner", "description": "保持节奏、同步进度"},
            {"role": "Member", "description": "主动共享成果与风险"},
        ]
        return GeneratedAdvice(
            topic_summary=topic_summary,
            knowledge_fields=knowledge_fields,
            learning_paths=learning_paths,
            methodology=methodology,
            team_tips=team_tips,
        )
