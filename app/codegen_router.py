import logging
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from anthropic import AsyncAnthropic

from app.config import Settings

logger = logging.getLogger(__name__)


class CodeGenType(StrEnum):
    HTML = 'html'
    MULTI_FILE = 'multi_file'
    VUE_PROJECT = 'vue_project'


class CodeGenTypeRouteModel(Protocol):
    async def choose_code_gen_type(self, init_prompt: str, system_prompt: str) -> str: ...


class CodeGenTypeRouter(Protocol):
    async def route(self, init_prompt: str) -> CodeGenType: ...

    async def aclose(self) -> None: ...


class AnthropicCodeGenTypeRouteModel:
    """通过 Anthropic 兼容协议执行一次轻量类型路由。"""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        if not settings.anthropic_api_key:
            raise RuntimeError('未配置 ANTHROPIC_API_KEY')
        self._client = client or AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            base_url=settings.anthropic_base_url,
            timeout=settings.anthropic_timeout_seconds,
        )
        self._model = settings.anthropic_model

    async def choose_code_gen_type(self, init_prompt: str, system_prompt: str) -> str:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=128,
            system=system_prompt,
            messages=[{'role': 'user', 'content': init_prompt}],
            tools=[
                {
                    'name': 'select_code_gen_type',
                    'description': '选择最合适的代码生成类型',
                    'input_schema': {
                        'type': 'object',
                        'properties': {
                            'codeGenType': {
                                'type': 'string',
                                'enum': [item.value for item in CodeGenType],
                            }
                        },
                        'required': ['codeGenType'],
                    },
                }
            ],
        )
        text_parts: list[str] = []
        for block in response.content:
            block_type = getattr(block, 'type', None)
            if block_type == 'tool_use':
                payload: Any = getattr(block, 'input', {})
                if isinstance(payload, dict) and payload.get('codeGenType'):
                    return str(payload['codeGenType'])
            elif block_type == 'text':
                text_parts.append(str(getattr(block, 'text', '')))
        return ''.join(text_parts).strip()

    async def aclose(self) -> None:
        await self._client.close()


class ModelBackedCodeGenTypeRouter:
    """把模型结果规范化为 codeGenType；失败时使用确定的 HTML 回退。"""

    def __init__(
        self,
        route_model: CodeGenTypeRouteModel,
        prompt_path: Path | None = None,
    ) -> None:
        self._route_model = route_model
        self._system_prompt = (
            prompt_path
            or Path(__file__).parent / 'prompts' / 'codegen-routing-system-prompt.txt'
        ).read_text(encoding='utf-8')

    async def route(self, init_prompt: str) -> CodeGenType:
        try:
            raw_type = await self._route_model.choose_code_gen_type(
                init_prompt, self._system_prompt
            )
            return CodeGenType(raw_type.strip().lower())
        except Exception as exc:
            logger.warning('代码生成类型路由失败，回退到 HTML: %s', exc)
            return CodeGenType.HTML

    async def aclose(self) -> None:
        closer = getattr(self._route_model, 'aclose', None)
        if closer is not None:
            await closer()


class UnavailableCodeGenTypeRouteModel:
    async def choose_code_gen_type(self, init_prompt: str, system_prompt: str) -> str:
        raise RuntimeError('模型路由能力不可用')

    async def aclose(self) -> None:
        return None


def create_code_gen_type_router(settings: Settings) -> CodeGenTypeRouter:
    if settings.anthropic_api_key:
        route_model: CodeGenTypeRouteModel = AnthropicCodeGenTypeRouteModel(settings)
    else:
        route_model = UnavailableCodeGenTypeRouteModel()
    return ModelBackedCodeGenTypeRouter(route_model)
