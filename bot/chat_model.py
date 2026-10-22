"""Provider configuration and text/vision calls, independent of Discord and images."""
import os
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class ChatConfig:
    provider: str
    api_key: str
    model: str
    base_url: str | None
    timeout: float

    @classmethod
    def from_env(cls):
        provider = os.getenv("CHAT_PROVIDER", "openai").strip().lower()
        if provider not in {"openai", "deepseek"}:
            raise ValueError("CHAT_PROVIDER must be openai or deepseek")
        deepseek = provider == "deepseek"
        prefix = "DEEPSEEK" if deepseek else "OPENAI"
        key = os.getenv("CHAT_API_KEY") or os.getenv(f"{prefix}_API_KEY", "")
        model = os.getenv("CHAT_MODEL") or ("deepseek-flash" if deepseek else os.getenv("OPENAI_MODEL", "gpt-5.6-luna"))
        base = os.getenv("CHAT_BASE_URL") or ("https://api.deepseek.com" if deepseek else os.getenv("OPENAI_BASE_URL"))
        if base:
            base = base.strip().rstrip("/") or None
        if base:
            parsed = urlparse(base)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("Chat base URL must be an absolute http(s) URL")
        return cls(provider, key.strip(), model.strip(), base, float(os.getenv("CHAT_TIMEOUT") or os.getenv("OPENAI_TIMEOUT", "60")))


class ChatModel:
    def __init__(self, config=None):
        self.config = config or ChatConfig.from_env()
        self._client = None

    @property
    def enabled(self):
        return bool(self.config.api_key)

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            kwargs = {"api_key": self.config.api_key, "timeout": self.config.timeout}
            if self.config.base_url:
                kwargs["base_url"] = self.config.base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def generate(self, *, instructions, text, image_urls=None, max_tokens=180, model=None):
        if not self.enabled:
            raise RuntimeError("Chat API key is missing")
        client = self._get_client()
        model = model or self.config.model
        if self.config.provider == "deepseek":
            content = [{"type": "text", "text": text}]
            content.extend({"type": "image_url", "image_url": {"url": url}} for url in (image_urls or [])[:4])
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": instructions}, {"role": "user", "content": content}],
                max_tokens=max_tokens,
                extra_body={"thinking": {"type": "disabled"}},
            )
            return (response.choices[0].message.content or "").strip()
        content = [{"type": "input_text", "text": text}]
        content.extend({"type": "input_image", "image_url": url} for url in (image_urls or [])[:4])
        response = await client.responses.create(
            model=model, instructions=instructions,
            input=[{"role": "user", "content": content}], max_output_tokens=max_tokens,
        )
        return (response.output_text or "").strip()
