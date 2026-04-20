from __future__ import annotations

from typing import Any, Sequence

from .config import (
    DEFAULT_OPENROUTER_MODEL,
    DEFAULT_REASONING_ENABLED,
    OPENROUTER_API_ENV,
    OpenRouterConfig,
    build_openrouter_config,
)
from .schemas import LLMCompletionResult


def get_openai_client_class():
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise ValueError("OpenAI SDK is not installed. Install project dependencies.") from exc
    return OpenAI


class OpenRouterProvider:
    def __init__(self, config: OpenRouterConfig | None = None, client: object | None = None) -> None:
        self.config = config or build_openrouter_config()
        self._client = client

    def complete(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        model: str | None = None,
        reasoning_enabled: bool = DEFAULT_REASONING_ENABLED,
        max_tokens: int = 256,
        temperature: float = 0.2,
    ) -> LLMCompletionResult:
        request_model = model or self.config.model or DEFAULT_OPENROUTER_MODEL
        request_payload: dict[str, Any] = {
            "model": request_model,
            "messages": list(messages),
            "max_tokens": max(1, int(max_tokens)),
            "temperature": float(temperature),
        }
        if reasoning_enabled:
            request_payload["extra_body"] = {"reasoning": {"enabled": True}}

        try:
            response = self._get_client().chat.completions.create(**request_payload)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(self._format_provider_error(exc)) from exc

        return normalize_completion_response(
            response,
            provider=self.config.provider,
            model=request_model,
        )

    def smoke(
        self,
        *,
        model: str | None = None,
        reasoning_enabled: bool = DEFAULT_REASONING_ENABLED,
        max_tokens: int = 64,
        temperature: float = 0.0,
    ) -> LLMCompletionResult:
        return self.complete(
            [{"role": "user", "content": "Return exactly: docgen-openrouter-ok"}],
            model=model,
            reasoning_enabled=reasoning_enabled,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def dry_run_status(self) -> dict[str, Any]:
        return {
            "provider": self.config.provider,
            "model": self.config.model,
            "base_url": self.config.base_url,
            "api_key_env": self.config.api_key_env,
            "key_present": self.config.key_present,
            "network_call": False,
        }

    def _get_client(self):
        if self._client is not None:
            return self._client

        if not self.config.api_key:
            raise ValueError(f"{OPENROUTER_API_ENV} is not set. Add it to environment or .env.")

        openai_client_class = get_openai_client_class()
        self._client = openai_client_class(
            base_url=self.config.base_url,
            api_key=self.config.api_key,
        )
        return self._client

    def _format_provider_error(self, exc: Exception) -> str:
        message = str(exc).strip() or exc.__class__.__name__
        if self.config.api_key:
            message = message.replace(self.config.api_key, "[redacted]")
        return f"OpenRouter request failed: {message}"


def normalize_completion_response(
    response: Any,
    *,
    provider: str,
    model: str,
) -> LLMCompletionResult:
    choices = getattr(response, "choices", None)
    if not choices:
        raise ValueError("OpenRouter response did not include any choices.")

    first_choice = choices[0]
    message = getattr(first_choice, "message", None)
    if message is None:
        raise ValueError("OpenRouter response choice did not include a message.")

    reasoning = getattr(message, "reasoning", None)
    reasoning_details = getattr(message, "reasoning_details", None)
    return LLMCompletionResult(
        provider=provider,
        model=model,
        content=normalize_message_content(getattr(message, "content", None)),
        reasoning=reasoning,
        reasoning_details_present=bool(reasoning_details),
        usage=normalize_usage(getattr(response, "usage", None)),
        raw_response_type=type(response).__name__,
        finish_reason=getattr(first_choice, "finish_reason", None),
        error=None,
    )


def normalize_message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text is not None:
                    parts.append(str(text))
                    continue
                parts.append(str(item))
                continue
            text = getattr(item, "text", None)
            if text is not None:
                parts.append(str(text))
                continue
            parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(content)


def normalize_usage(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    if isinstance(usage, dict):
        return dict(sorted(usage.items()))

    payload: dict[str, Any] = {}
    for field_name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, field_name, None)
        if value is not None:
            payload[field_name] = value
    return payload or None
