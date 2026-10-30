from collections.abc import Callable

from app.codegen_router import CodeGenType
from app.config import Settings
from app.runtime.anthropic_model import (
    AnthropicHtmlModel,
    AnthropicMultiFileModel,
    AnthropicVueModel,
)
from app.runtime.model import AgentModel, UnavailableAgentModel


def create_agent_model_factory(settings: Settings) -> Callable[[str], AgentModel]:
    if not settings.anthropic_api_key:
        return lambda _: UnavailableAgentModel()

    def factory(raw_type: str) -> AgentModel:
        code_gen_type = CodeGenType(raw_type)
        if code_gen_type is CodeGenType.HTML:
            return AnthropicHtmlModel(settings)
        if code_gen_type is CodeGenType.MULTI_FILE:
            return AnthropicMultiFileModel(settings)
        if code_gen_type is CodeGenType.VUE_PROJECT:
            return AnthropicVueModel(settings)
        raise ValueError(f"暂不支持的 codeGenType: {raw_type}")

    return factory
