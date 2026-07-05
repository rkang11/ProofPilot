from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class CandidateResponse:
    code: str
    provider: str
    model: str
    raw_text: str
    error: str | None = None


@dataclass
class TextResponse:
    text: str
    provider: str
    model: str
    error: str | None = None


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str | None = None) -> None:
        self.api_key = api_key
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")

    def generate_repair_candidate(self, prompt: str) -> CandidateResponse:
        request_prompt = "\n".join(
            [
                prompt,
                "",
                "Return only one complete Lean 4 proof.",
                "Prefer a fenced ```lean code block. Do not use `sorry`.",
            ]
        )
        response = self.generate_text(request_prompt, temperature=0.1, max_output_tokens=2048)
        return CandidateResponse(
            code=_extract_lean_code(response.text),
            provider=response.provider,
            model=response.model,
            raw_text=response.text,
            error=response.error,
        )

    def generate_text(
        self,
        prompt: str,
        *,
        temperature: float = 0.2,
        max_output_tokens: int = 4096,
    ) -> TextResponse:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "topP": 0.9,
                "maxOutputTokens": max_output_tokens,
            },
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=30, context=_ssl_context()) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            return TextResponse(
                text="",
                provider=self.name,
                model=self.model,
                error=_format_http_error(exc.code, error_body),
            )
        except (urllib.error.URLError, TimeoutError) as exc:
            return TextResponse(
                text="",
                provider=self.name,
                model=self.model,
                error=f"Gemini request failed: {exc}",
            )

        raw_text = _extract_text(body)
        return TextResponse(text=raw_text, provider=self.name, model=self.model)


def get_repair_provider() -> GeminiProvider | None:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    return GeminiProvider(api_key=api_key)


def get_text_provider() -> GeminiProvider | None:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    return GeminiProvider(api_key=api_key)


def _ssl_context() -> ssl.SSLContext:
    for cafile in _candidate_ca_files():
        if cafile and os.path.exists(cafile):
            return ssl.create_default_context(cafile=cafile)
    return ssl.create_default_context()


def _candidate_ca_files() -> list[str | None]:
    candidates = [os.environ.get("SSL_CERT_FILE")]
    try:
        import certifi

        candidates.append(certifi.where())
    except ImportError:
        pass
    candidates.append("/etc/ssl/cert.pem")
    return candidates


def _extract_text(body: dict[str, object]) -> str:
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return ""

    content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
    if not isinstance(content, dict):
        return ""

    parts = content.get("parts")
    if not isinstance(parts, list):
        return ""

    text_parts = [part.get("text", "") for part in parts if isinstance(part, dict)]
    return "\n".join(part for part in text_parts if isinstance(part, str)).strip()


def _extract_lean_code(text: str) -> str:
    fenced = re.search(r"```(?:lean|lean4)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    return text.strip()


def _format_http_error(status_code: int, body: str) -> str:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        compact = " ".join(body.split())
        return f"Gemini HTTP {status_code}: {compact[:240]}"

    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return f"Gemini HTTP {status_code}."

    message = str(error.get("message", "")).strip()
    status = str(error.get("status", "")).strip()
    if message and status:
        return f"Gemini HTTP {status_code} ({status}): {message}"
    if message:
        return f"Gemini HTTP {status_code}: {message}"
    return f"Gemini HTTP {status_code}."
