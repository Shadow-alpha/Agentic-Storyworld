from __future__ import annotations

import ast
import json
import random
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Iterator


@dataclass
class AgentLLMConfig:
    enabled: bool
    thinking: bool
    api_key: str | None
    model: str
    base_url: str | None
    max_retries: int
    retry_base_delay: float


def read_llm_config(llm_config: Any) -> AgentLLMConfig:
    llm_config = llm_config if isinstance(llm_config, dict) else {}
    return AgentLLMConfig(
        enabled=bool(llm_config.get("enabled", False)),
        thinking=bool(llm_config.get("thinking", False)),
        api_key=llm_config.get("api_key") or None,
        model=str(llm_config.get("model", "MiniMax-M2.7")),
        base_url=llm_config.get("base_url") or None,
        max_retries=max(1, int(llm_config.get("max_retries", 3))),
        retry_base_delay=max(0.1, float(llm_config.get("retry_base_delay", 1.0))),
    )


class OpenAIClient:
    """Small wrapper around the OpenAI SDK with graceful fallback expectations."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: int = 30,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        thinking: bool = False,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.retry_base_delay = max(0.1, retry_base_delay)
        self.thinking = thinking
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        def _request() -> str:
            client = self._get_client()
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                timeout=self.timeout,
                **self._extra_body_kwargs(),
            )
            return response.choices[0].message.content

        return self._with_retry(_request, op_name="generate_text")

    def stream_text(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        attempt = 0
        while True:
            stream_started = False
            try:
                client = self._get_client()
                stream = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    timeout=self.timeout,
                    stream=True,
                    **self._extra_body_kwargs(),
                )
                for chunk in stream:
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    delta = getattr(choices[0], "delta", None)
                    content = getattr(delta, "content", None) if delta else None
                    if isinstance(content, list):
                        for item in content:
                            text = getattr(item, "text", None) if not isinstance(item, dict) else item.get("text")
                            if text:
                                stream_started = True
                                yield text
                    elif content:
                        stream_started = True
                        yield content
                return
            except Exception as exc:
                attempt += 1
                if stream_started or attempt >= self.max_retries or not self._is_retryable_error(exc):
                    raise
                self._log_retry("stream_text", attempt, exc)
                time.sleep(self._retry_delay(attempt))

    def _extra_body_kwargs(self) -> dict[str, Any]:
        extra_body = self._build_extra_body()
        return {"extra_body": extra_body} if extra_body else {}

    def _build_extra_body(self) -> dict[str, Any]:
        if self.model.lower().startswith("minimax"):
            return {
                "thinking": {"type": "adaptive" if self.thinking else "disabled"},
                "reasoning_split": False,
            }
        return {}

    def _extract_json(self, text: str) -> dict[str, Any]:
        cleaned = text.strip().strip("```json").strip("```").strip()
        return json.loads(cleaned)

    def _extract_xml(self, text: str) -> str:
        cleaned = text.strip().strip("```xml").strip("```").strip()
        if not cleaned:
            raise ValueError("Empty XML payload.")
        return cleaned

    def generate_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        text = self.generate_text(system_prompt=system_prompt, user_prompt=user_prompt)
        try:
            return self._extract_json(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Model did not return valid JSON: {text}") from exc

    def parse_json_text(self, text: str) -> dict[str, Any]:
        return self._extract_json(text)

    def parse_xml_text(self, text: str) -> ET.Element:
        cleaned = self._extract_xml(text)
        try:
            return ET.fromstring(cleaned)
        except ET.ParseError:
            try:
                return ET.fromstring(f"<root>{cleaned}</root>")
            except ET.ParseError as exc:
                raise ValueError(f"Model did not return valid XML: {cleaned}") from exc

    def parse_kv_block(self, text: str) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            separator = ":" if ":" in line else "=" if "=" in line else None
            if separator is None:
                continue
            key, raw_value = line.split(separator, 1)
            key = key.strip()
            if not key:
                continue
            value = self._coerce_scalar(raw_value.strip())
            self._assign_nested(data, key, value)
        return data

    def _assign_nested(self, target: dict[str, Any], dotted_key: str, value: Any) -> None:
        parts = [part.strip() for part in dotted_key.split(".") if part.strip()]
        if not parts:
            return
        cursor = target
        for part in parts[:-1]:
            existing = cursor.get(part)
            if not isinstance(existing, dict):
                existing = {}
                cursor[part] = existing
            cursor = existing
        cursor[parts[-1]] = value

    def _coerce_scalar(self, value: str) -> Any:
        if value == "":
            return ""
        lowered = value.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        if lowered == "null":
            return None
        try:
            if value.startswith("{") or value.startswith("["):
                return json.loads(value)
        except json.JSONDecodeError:
            pass
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return value

    def _with_retry(self, operation, op_name: str) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return operation()
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries or not self._is_retryable_error(exc):
                    raise
                self._log_retry(op_name, attempt, exc)
                time.sleep(self._retry_delay(attempt))
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"{op_name} failed without raising an exception.")

    def _retry_delay(self, attempt: int) -> float:
        base = self.retry_base_delay * (2 ** max(0, attempt - 1))
        jitter = random.uniform(0, base * 0.2)
        return base + jitter

    def _is_retryable_error(self, exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int):
            if status_code in {408, 409, 429} or 500 <= status_code < 600:
                return True
            if 400 <= status_code < 500:
                return False

        error_type = type(exc).__name__.lower()
        message = str(exc).lower()
        retryable_markers = (
            "timeout",
            "timed out",
            "temporarily unavailable",
            "connection",
            "connect",
            "network",
            "reset by peer",
            "broken pipe",
            "overload",
            "overloaded",
            "rate limit",
            "too many requests",
            "service unavailable",
            "bad gateway",
            "gateway timeout",
            "server error",
        )
        non_retryable_markers = (
            "invalid api key",
            "authentication",
            "permission",
            "unauthorized",
            "forbidden",
            "bad request",
            "invalid request",
            "unsupported",
            "not found",
        )
        if any(marker in message for marker in non_retryable_markers):
            return False
        if any(marker in message for marker in retryable_markers):
            return True
        if any(marker in error_type for marker in ("timeout", "connection", "rate", "internalserver", "apierror")):
            return True
        return False

    def _log_retry(self, op_name: str, attempt: int, exc: Exception) -> None:
        print(f"[llm retry] op={op_name} attempt={attempt} error={type(exc).__name__}: {exc}")
