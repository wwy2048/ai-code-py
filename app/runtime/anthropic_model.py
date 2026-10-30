import asyncio
from pathlib import Path
from typing import Any, ClassVar, cast

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncAnthropic,
    InternalServerError,
    RateLimitError,
)

from app.config import Settings
from app.runtime.model import ModelCallError, ModelReply, TextEmitter, ToolCall
from app.runtime.tools import TOOL_DEFINITIONS


class AnthropicCodeModel:
    """通过 Anthropic 兼容协议执行代码生成工具循环。"""

    prompt_file: ClassVar[str]

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        if not settings.anthropic_api_key and client is None:
            raise RuntimeError('未配置 ANTHROPIC_API_KEY')
        self._client = client or AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            base_url=settings.anthropic_base_url,
            timeout=settings.anthropic_timeout_seconds,
        )
        self._model = settings.anthropic_model
        self._max_tokens = settings.anthropic_max_tokens
        self._max_retries = settings.model_max_retries
        self._retry_base_seconds = settings.model_retry_base_seconds
        self._system_prompt = (
            Path(__file__).parent.parent / 'prompts' / self.prompt_file
        ).read_text(encoding='utf-8')

    @property
    def model_name(self) -> str:
        return self._model

    async def next_turn(
        self,
        messages: list[dict[str, Any]],
        on_text: TextEmitter,
    ) -> ModelReply:
        emitted = False
        for attempt in range(self._max_retries):
            try:
                async with self._client.messages.stream(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    system=self._system_prompt,
                    messages=cast(Any, self._to_api_messages(messages)),
                    tools=cast(Any, TOOL_DEFINITIONS),
                ) as stream:
                    async for delta in stream.text_stream:
                        if delta:
                            emitted = True
                            await on_text(delta)
                    final = await stream.get_final_message()
                return self._model_reply(final, retry_count=attempt)
            except Exception as exc:
                retryable = self._is_retryable(exc)
                if emitted or not retryable or attempt + 1 >= self._max_retries:
                    raise ModelCallError(self._error_category(exc), attempt) from exc
                await asyncio.sleep(self._retry_base_seconds * (2**attempt))
        raise RuntimeError('模型调用次数耗尽')

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(
            exc,
            (
                TimeoutError,
                ConnectionError,
                APITimeoutError,
                APIConnectionError,
                RateLimitError,
                InternalServerError,
            ),
        ):
            return True
        return isinstance(exc, APIStatusError) and (
            exc.status_code == 429 or exc.status_code >= 500
        )

    @staticmethod
    def _error_category(exc: Exception) -> str:
        if isinstance(exc, (TimeoutError, APITimeoutError)):
            return 'timeout'
        if isinstance(exc, (ConnectionError, APIConnectionError)):
            return 'connection'
        if isinstance(exc, RateLimitError):
            return 'rate_limit'
        if isinstance(exc, InternalServerError):
            return 'server_error'
        if isinstance(exc, APIStatusError):
            return f'http_{exc.status_code}'
        return type(exc).__name__

    def _model_reply(self, final: Any, *, retry_count: int) -> ModelReply:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in final.content:
            block_type = getattr(block, 'type', None)
            if block_type == 'text':
                text_parts.append(str(getattr(block, 'text', '')))
            elif block_type == 'tool_use':
                arguments = getattr(block, 'input', {})
                if isinstance(arguments, dict):
                    tool_calls.append(
                        ToolCall(
                            id=str(getattr(block, 'id', '')),
                            name=str(getattr(block, 'name', '')),
                            arguments=arguments,
                        )
                    )
        usage = getattr(final, 'usage', None)
        return ModelReply(
            text=''.join(text_parts),
            tool_calls=tool_calls,
            input_tokens=int(getattr(usage, 'input_tokens', 0) or 0),
            output_tokens=int(getattr(usage, 'output_tokens', 0) or 0),
            model_name=self._model,
            retry_count=retry_count,
        )

    @staticmethod
    def _to_api_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        api_messages: list[dict[str, Any]] = []
        pending_tool_results: list[dict[str, Any]] = []

        def flush_tool_results() -> None:
            if pending_tool_results:
                api_messages.append(
                    {'role': 'user', 'content': list(pending_tool_results)}
                )
                pending_tool_results.clear()

        for message in messages:
            role = message.get('role')
            if role == 'tool_result':
                block: dict[str, Any] = {
                    'type': 'tool_result',
                    'tool_use_id': message.get('tool_call_id'),
                    'content': str(message.get('content', '')),
                }
                if message.get('is_error'):
                    block['is_error'] = True
                pending_tool_results.append(block)
                continue
            flush_tool_results()
            if role == 'user':
                api_messages.append({'role': 'user', 'content': message.get('content', '')})
            elif role == 'assistant':
                content: list[dict[str, Any]] = []
                text = str(message.get('content', ''))
                if text:
                    content.append({'type': 'text', 'text': text})
                for call in message.get('tool_calls', []):
                    content.append(
                        {
                            'type': 'tool_use',
                            'id': call['id'],
                            'name': call['name'],
                            'input': call['arguments'],
                        }
                    )
                api_messages.append({'role': 'assistant', 'content': content})
        flush_tool_results()
        return api_messages


class AnthropicHtmlModel(AnthropicCodeModel):
    prompt_file = 'codegen-html-runtime-system-prompt.txt'


class AnthropicMultiFileModel(AnthropicCodeModel):
    prompt_file = 'codegen-multi-file-runtime-system-prompt.txt'
