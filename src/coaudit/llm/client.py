import json
import os
import re
from typing import Any, Optional, Protocol

import httpx
from dotenv import load_dotenv

EXCLUDED_MODEL_WORDS = ("lite", "image", "tts", "audio", "live", "embedding", "vision")


class LlmError(Exception):
    pass


class LlmClient(Protocol):
    def complete_json(self, prompt: str) -> dict[str, Any]: ...


def parse_json_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise LlmError("The model did not return valid JSON") from error
    if not isinstance(data, dict):
        raise LlmError("The model returned JSON that is not an object")
    return data


class HttpLlmClient:
    default_model = ""

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = 60.0,
    ) -> None:
        if not api_key or not api_key.strip():
            raise LlmError("LLM_API_KEY is empty")
        self.api_key = api_key.strip()
        self.model = model or self.default_model
        self._http = httpx.Client(transport=transport, timeout=timeout)

    def _send(self, method: str, url: str, headers: dict[str, str], body=None) -> dict[str, Any]:
        try:
            response = self._http.request(method, url, headers=headers, json=body)
        except httpx.HTTPError as error:
            raise LlmError(
                f"Could not reach the model service ({type(error).__name__})"
            ) from error
        if response.status_code != 200:
            raise LlmError(f"The model service returned status {response.status_code}")
        try:
            return response.json()
        except ValueError as error:
            raise LlmError("The model service returned an unreadable response") from error


class GeminiClient(HttpLlmClient):
    base_url = "https://generativelanguage.googleapis.com/v1beta"

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.api_key}

    def _resolve_model(self) -> str:
        if self.model:
            return self.model if self.model.startswith("models/") else f"models/{self.model}"
        data = self._send("GET", f"{self.base_url}/models", self._headers())
        candidates = []
        for item in data.get("models", []):
            name = item.get("name", "")
            if "generateContent" not in item.get("supportedGenerationMethods", []):
                continue
            if "flash" in name and not any(word in name for word in EXCLUDED_MODEL_WORDS):
                candidates.append(name)
        if not candidates:
            raise LlmError("No suitable Gemini model was found for this key")
        self.model = sorted(candidates)[-1]
        return self.model

    def complete_json(self, prompt: str) -> dict[str, Any]:
        model = self._resolve_model()
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        data = self._send("POST", f"{self.base_url}/{model}:generateContent", self._headers(), body)
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as error:
            raise LlmError("The Gemini response had no text") from error
        return parse_json_text(text)


class OpenAIClient(HttpLlmClient):
    default_model = "gpt-4o-mini"
    url = "https://api.openai.com/v1/chat/completions"

    def complete_json(self, prompt: str) -> dict[str, Any]:
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        data = self._send("POST", self.url, headers, body)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise LlmError("The OpenAI response had no text") from error
        return parse_json_text(text)


class AnthropicClient(HttpLlmClient):
    default_model = "claude-haiku-5-5"
    url = "https://api.anthropic.com/v1/messages"

    def complete_json(self, prompt: str) -> dict[str, Any]:
        body = {
            "model": self.model,
            "max_tokens": 4000,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        data = self._send("POST", self.url, headers, body)
        try:
            text = data["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as error:
            raise LlmError("The Claude response had no text") from error
        return parse_json_text(text)


PROVIDERS = {
    "gemini": GeminiClient,
    "openai": OpenAIClient,
    "anthropic": AnthropicClient,
    "claude": AnthropicClient,
}


def client_from_environment(
    transport: Optional[httpx.BaseTransport] = None,
) -> Optional[LlmClient]:
    load_dotenv()
    key = os.environ.get("LLM_API_KEY", "").strip()
    if not key:
        return None
    provider = os.environ.get("LLM_PROVIDER", "gemini").strip().lower()
    if provider not in PROVIDERS:
        raise LlmError(f"Unknown LLM_PROVIDER {provider!r}: use gemini, openai or anthropic")
    model = os.environ.get("LLM_MODEL", "").strip() or None
    return PROVIDERS[provider](key, model, transport=transport)
