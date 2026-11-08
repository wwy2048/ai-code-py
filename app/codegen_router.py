import json
import logging
import re
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from anthropic import AsyncAnthropic

from app.config import Settings

logger = logging.getLogger(__name__)

_MAX_LOG_TEXT_CHARS = 20_000
_PROMPT_PREVIEW_CHARS = 1_000


class CodeGenType(StrEnum):
    HTML = "html"
    MULTI_FILE = "multi_file"
    VUE_PROJECT = "vue_project"


@dataclass(frozen=True, slots=True)
class CodeGenTypeRouteResult:
    """一次类型路由的规范化结果，保留原始响应供异常排查。"""

    selected_type: str
    raw_response: str
    model: str | None = None
    duration_ms: float = 0.0
    attempts: int = 1
    format_error: str | None = None


class CodeGenTypeRouteModelError(RuntimeError):
    """携带路由模型调用上下文，避免异常在回退时丢失关键信息。"""

    def __init__(
        self,
        message: str,
        *,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        duration_ms: float | None = None,
        request_id: str | None = None,
        raw_response: str | None = None,
    ) -> None:
        super().__init__(message)
        self.model = model
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.duration_ms = duration_ms
        self.request_id = request_id
        self.raw_response = raw_response


class CodeGenTypeRouteModel(Protocol):
    async def choose_code_gen_type(
        self, init_prompt: str, system_prompt: str
    ) -> CodeGenTypeRouteResult: ...


class CodeGenTypeRouter(Protocol):
    async def route(self, init_prompt: str) -> CodeGenType: ...

    async def aclose(self) -> None: ...


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_CODE_GEN_TYPE_ALIASES = {
    "html": CodeGenType.HTML,
    "single": CodeGenType.HTML,
    "single_file": CodeGenType.HTML,
    "multi": CodeGenType.MULTI_FILE,
    "multi_file": CodeGenType.MULTI_FILE,
    "multifile": CodeGenType.MULTI_FILE,
    "vue": CodeGenType.VUE_PROJECT,
    "vue.js": CodeGenType.VUE_PROJECT,
    "vue3": CodeGenType.VUE_PROJECT,
    "vue_js": CodeGenType.VUE_PROJECT,
    "vue_project": CodeGenType.VUE_PROJECT,
    "vuejs": CodeGenType.VUE_PROJECT,
}


def _normalize_code_gen_type(value: Any) -> CodeGenType | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().strip("\"'").lower()
    normalized = re.sub(r"[\s-]+", "_", normalized)
    return _CODE_GEN_TYPE_ALIASES.get(normalized)


def _fallback_code_gen_type(_init_prompt: str) -> CodeGenType:
    """任何路由失败都使用最稳定的 HTML 回退。"""
    return CodeGenType.HTML


def _fallback_reason(_init_prompt: str, _fallback_type: CodeGenType) -> str:
    return "default_html"


def _extract_json_payload(text: str) -> dict[str, Any] | None:
    """从严格 JSON、Markdown 代码块或夹带说明的文本中提取对象。"""
    source = text.strip().lstrip("\ufeff")
    candidates: list[str] = []
    fenced = _FENCED_JSON_RE.search(source)
    if fenced is not None:
        candidates.append(fenced.group(1).strip())
    candidates.append(source)

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload

    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", source):
        try:
            payload, _ = decoder.raw_decode(source[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "codeGenType" in payload:
            return payload
    return None


def _truncate_for_log(text: str, limit: int = _MAX_LOG_TEXT_CHARS) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}... [日志截断 {omitted} 字符]"


def _serialize_model_response(response: Any) -> str:
    """只序列化模型响应本身，不读取请求头，避免日志泄露鉴权信息。"""
    try:
        if hasattr(response, "model_dump"):
            payload = response.model_dump(mode="json", exclude_none=True)
        elif hasattr(response, "dict"):
            payload = response.dict()
        else:
            payload = response
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    except Exception:
        text = repr(response)
    return _truncate_for_log(text)


def _serialize_error_body(exc: Exception) -> str | None:
    body = getattr(exc, "body", None)
    if body is None:
        return None
    if isinstance(body, str):
        return _truncate_for_log(body)
    try:
        text = json.dumps(body, ensure_ascii=False, indent=2, default=str)
    except Exception:
        text = repr(body)
    return _truncate_for_log(text)


def _request_id_from_error(exc: Exception) -> str | None:
    request_id = getattr(exc, "request_id", None)
    if request_id:
        return str(request_id)
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        request_id = headers.get("request-id") or headers.get("x-request-id")
        if request_id:
            return str(request_id)
    return None


def _elapsed_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000


def _extract_text_from_response(response: Any) -> str:
    return "".join(
        str(getattr(block, "text", ""))
        for block in response.content
        if getattr(block, "type", None) == "text"
    ).strip()


def _parse_route_output(raw_text: str) -> tuple[str, str | None]:
    payload = _extract_json_payload(raw_text)
    if payload is None:
        return raw_text.strip(), "响应中未找到合法 JSON 对象"
    value = payload.get("codeGenType")
    if not isinstance(value, str) or not value.strip():
        return "", "JSON 缺少非空 codeGenType 字段"
    return value.strip(), None


def _build_repair_prompt(init_prompt: str, invalid_output: str, error: str) -> str:
    previous_output = _truncate_for_log(invalid_output or "<空响应>", 2_000)
    return (
        "原始用户需求：\n"
        f"{init_prompt}\n\n"
        "你上一次的输出是：\n"
        f"{previous_output}\n\n"
        f"上一次输出不符合要求：{error}。\n"
        "请重新分类。只输出一个合法 JSON 对象，不要输出 Markdown、代码块或解释。\n"
        '格式必须为：{"codeGenType":"html"}\n'
        "codeGenType 只能是：html、multi_file、vue_project。"
    )


def _combine_raw_responses(responses: list[str]) -> str:
    if len(responses) == 1:
        return responses[0]
    return "\n\n".join(
        f"--- attempt {index} ---\n{response}"
        for index, response in enumerate(responses, start=1)
    )


class AnthropicCodeGenTypeRouteModel:
    """通过 Anthropic 兼容协议执行一次严格 JSON 类型路由。"""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        if not settings.anthropic_api_key:
            raise RuntimeError("未配置 ANTHROPIC_API_KEY")
        self._client = client or AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            base_url=settings.anthropic_base_url,
            timeout=settings.anthropic_timeout_seconds,
        )
        self._model = settings.anthropic_model
        self._base_url = settings.anthropic_base_url
        self._timeout_seconds = settings.anthropic_timeout_seconds

    async def _request_model(self, user_prompt: str, system_prompt: str) -> Any:
        started_at = time.perf_counter()
        try:
            return await self._client.messages.create(
                model=self._model,
                max_tokens=64,
                temperature=0,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as exc:
            raise CodeGenTypeRouteModelError(
                f"{type(exc).__name__}: {exc}",
                model=self._model,
                base_url=self._base_url,
                timeout_seconds=self._timeout_seconds,
                duration_ms=_elapsed_ms(started_at),
                request_id=_request_id_from_error(exc),
                raw_response=_serialize_error_body(exc),
            ) from exc

    async def choose_code_gen_type(
        self, init_prompt: str, system_prompt: str
    ) -> CodeGenTypeRouteResult:
        started_at = time.perf_counter()
        response = await self._request_model(init_prompt, system_prompt)
        raw_response = _serialize_model_response(response)
        raw_text = _extract_text_from_response(response)
        selected_type, format_error = _parse_route_output(raw_text)

        if _normalize_code_gen_type(selected_type) is None:
            if format_error is None:
                format_error = f"codeGenType 不在允许值内: {selected_type!r}"
            repair_prompt = _build_repair_prompt(init_prompt, raw_text, format_error)
            try:
                repair_response = await self._request_model(repair_prompt, system_prompt)
            except CodeGenTypeRouteModelError as exc:
                exc.raw_response = _combine_raw_responses(
                    [raw_response, exc.raw_response or "<unavailable>"]
                )
                raise
            repair_raw_response = _serialize_model_response(repair_response)
            repair_text = _extract_text_from_response(repair_response)
            selected_type, format_error = _parse_route_output(repair_text)
            if _normalize_code_gen_type(selected_type) is None and format_error is None:
                format_error = f"codeGenType 不在允许值内: {selected_type!r}"
            raw_response = _combine_raw_responses([raw_response, repair_raw_response])
            attempts = 2
        else:
            attempts = 1

        return CodeGenTypeRouteResult(
            selected_type=selected_type,
            raw_response=raw_response,
            model=self._model,
            duration_ms=_elapsed_ms(started_at),
            attempts=attempts,
            format_error=format_error,
        )

    async def aclose(self) -> None:
        await self._client.close()

class ModelBackedCodeGenTypeRouter:
    """把模型结果规范化为 codeGenType；失败时无条件回退到 HTML。"""

    def __init__(
        self,
        route_model: CodeGenTypeRouteModel,
        prompt_path: Path | None = None,
    ) -> None:
        self._route_model = route_model
        self._system_prompt = (
            prompt_path or Path(__file__).parent / "prompts" / "codegen-routing-system-prompt.txt"
        ).read_text(encoding="utf-8")

    async def route(self, init_prompt: str) -> CodeGenType:
        started_at = time.perf_counter()
        try:
            model_result = await self._route_model.choose_code_gen_type(
                init_prompt, self._system_prompt
            )
        except Exception as exc:
            cause = exc.__cause__ or exc.__context__
            duration_ms = getattr(exc, "duration_ms", None)
            if duration_ms is None:
                duration_ms = _elapsed_ms(started_at)
            raw_response = getattr(exc, "raw_response", None)
            fallback_type = _fallback_code_gen_type(init_prompt)
            logger.warning(
                "代码生成类型路由失败，回退到 HTML | stage=model_call "
                "fallback_type=%s fallback_reason=%s error_type=%s error=%r "
                "cause_type=%s cause=%r model=%s base_url=%s timeout_seconds=%s "
                "duration_ms=%.1f request_id=%r raw_model_response=%s prompt_preview=%r",
                fallback_type.value,
                _fallback_reason(init_prompt, fallback_type),
                type(exc).__name__,
                str(exc),
                type(cause).__name__ if cause is not None else None,
                str(cause) if cause is not None else None,
                getattr(exc, "model", None),
                getattr(exc, "base_url", None),
                getattr(exc, "timeout_seconds", None),
                duration_ms,
                getattr(exc, "request_id", None),
                raw_response if raw_response is not None else "<unavailable>",
                _truncate_for_log(init_prompt, _PROMPT_PREVIEW_CHARS),
                exc_info=True,
            )
            return fallback_type

        raw_type = model_result.selected_type
        normalized_type = raw_type.strip().lower()
        code_gen_type = _normalize_code_gen_type(raw_type)
        if code_gen_type is None:
            fallback_type = _fallback_code_gen_type(init_prompt)
            logger.warning(
                "代码生成类型路由结果解析失败，回退到 HTML | stage=parse_result "
                "fallback_type=%s fallback_reason=%s raw_model_output=%r "
                "normalized_model_output=%r format_error=%r attempts=%s model=%s "
                "duration_ms=%.1f prompt_preview=%r raw_model_response=%s",
                fallback_type.value,
                _fallback_reason(init_prompt, fallback_type),
                raw_type,
                normalized_type,
                model_result.format_error,
                model_result.attempts,
                model_result.model,
                _elapsed_ms(started_at),
                _truncate_for_log(init_prompt, _PROMPT_PREVIEW_CHARS),
                model_result.raw_response or "<empty>",
            )
            return fallback_type

        logger.debug(
            "代码生成类型路由成功 code_gen_type=%s raw_model_output=%r model=%s duration_ms=%.1f",
            code_gen_type.value,
            raw_type,
            model_result.model,
            _elapsed_ms(started_at),
        )
        return code_gen_type

    async def aclose(self) -> None:
        closer = getattr(self._route_model, "aclose", None)
        if closer is not None:
            await closer()


class UnavailableCodeGenTypeRouteModel:
    async def choose_code_gen_type(
        self, init_prompt: str, system_prompt: str
    ) -> CodeGenTypeRouteResult:
        raise RuntimeError("模型路由能力不可用")

    async def aclose(self) -> None:
        return None


def create_code_gen_type_router(settings: Settings) -> CodeGenTypeRouter:
    if settings.anthropic_api_key:
        route_model: CodeGenTypeRouteModel = AnthropicCodeGenTypeRouteModel(settings)
    else:
        route_model = UnavailableCodeGenTypeRouteModel()
    return ModelBackedCodeGenTypeRouter(route_model)
