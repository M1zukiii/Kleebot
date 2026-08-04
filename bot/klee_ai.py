import base64
import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PROMPT_FILE = BASE_DIR / "klee_prompt.txt"

KLEE_SYSTEM_PROMPT = """你是 Discord 机器人 Kleebot。回复时以 Klee 的人设说话。
自称 Klee，语气活泼、天真、热情，回复短一点。不要说自己是 AI、语言模型或 API。
遇到危险、违法、现实伤害内容时，用 Klee 的语气拒绝或转移话题。"""

FALLBACK_REPLY = "西风骑士团，「火花骑士」，Klee，前来报到！…呃—后面该说什么词来着？Klee背不下来啦..."


class KleeAI:
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.model = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
        self.image_model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1.5")
        self.image_size = os.getenv("OPENAI_IMAGE_SIZE", "1024x1024")
        self.base_url = os.getenv("OPENAI_BASE_URL")
        self.prompt_file = Path(os.getenv("KLEE_PROMPT_FILE", str(DEFAULT_PROMPT_FILE)))
        self._client = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI

            kwargs = {"api_key": self.api_key, "timeout": 20.0}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    def _system_prompt(self) -> str:
        try:
            prompt = self.prompt_file.read_text(encoding="utf-8").strip()
        except OSError:
            return KLEE_SYSTEM_PROMPT
        return prompt or KLEE_SYSTEM_PROMPT

    async def reply(
        self,
        *,
        user_name: str,
        message: str,
        image_urls: list[str] | None = None,
        context: list[str] | None = None,
        video_metadata: list[str] | None = None,
    ) -> str:
        if not self.enabled:
            return FALLBACK_REPLY

        context_text = "\n".join(context or [])
        text_prompt = (
            f"Discord 用户 {user_name} 对 Klee 说：\n"
            f"{message.strip() or '只是在叫 Klee。'}\n\n"
            "请用 Klee 的人设直接回复这个用户。"
        )
        if context_text:
            text_prompt += f"\n\n这是同一频道最近几次 @Klee 的对话上下文，只在有帮助时参考，不要逐字复述：\n{context_text}"
        if video_metadata:
            text_prompt += "\n\n用户发了视频链接，这是能拿到的 metadata。请根据这些信息用 Klee 的语气简短总结或吐槽，不要假装看过完整视频：\n"
            text_prompt += "\n\n".join(video_metadata)
        if image_urls:
            text_prompt += "\n如果用户附带了图片，请先看图片内容，再自然地一起回应。"

        content = [{"type": "input_text", "text": text_prompt}]
        for image_url in (image_urls or [])[:4]:
            content.append({"type": "input_image", "image_url": image_url})

        try:
            response = await self._get_client().responses.create(
                model=self.model,
                instructions=self._system_prompt(),
                input=[{"role": "user", "content": content}],
                max_output_tokens=180,
            )
            text = (response.output_text or "").strip()
        except Exception as exc:
            print(f"klee ai reply failed: {exc}", flush=True)
            return FALLBACK_REPLY

        if not text:
            return FALLBACK_REPLY
        return text[:1800]

    async def generate_image(self, *, prompt: str) -> bytes:
        if not self.enabled:
            raise RuntimeError("OPENAI_API_KEY is missing.")
        response = await self._get_client().images.generate(
            model=self.image_model,
            prompt=prompt,
            size=self.image_size,
            n=1,
        )
        if not response.data:
            raise RuntimeError("Image generation returned no data.")

        item = response.data[0]
        b64_json = getattr(item, "b64_json", None)
        if b64_json:
            return base64.b64decode(b64_json)

        image_url = getattr(item, "url", None)
        if image_url:
            from urllib.request import urlopen

            with urlopen(image_url, timeout=30) as result:
                return result.read()

        raise RuntimeError("Image generation did not return image bytes.")
