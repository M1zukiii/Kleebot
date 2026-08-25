import base64
import io
import os
import random
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import httpx


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
        self.timeout = float(os.getenv("OPENAI_TIMEOUT", "60"))
        self.image_timeout = float(os.getenv("OPENAI_IMAGE_TIMEOUT", "180"))
        self.base_url = self._clean_base_url(os.getenv("OPENAI_BASE_URL"))
        self.image_provider = os.getenv("IMAGE_PROVIDER", "openai").strip().lower()
        self.novelai_token = os.getenv("NOVELAI_TOKEN")
        self.novelai_model = os.getenv("NOVELAI_MODEL", "nai-diffusion-4-5-full")
        self.novelai_width = int(os.getenv("NOVELAI_WIDTH", "1024"))
        self.novelai_height = int(os.getenv("NOVELAI_HEIGHT", "1024"))
        self.novelai_steps = int(os.getenv("NOVELAI_STEPS", "28"))
        self.novelai_scale = float(os.getenv("NOVELAI_SCALE", "6.0"))
        self.novelai_sampler = os.getenv("NOVELAI_SAMPLER", "k_euler_ancestral")
        self.novelai_negative_prompt = os.getenv(
            "NOVELAI_NEGATIVE_PROMPT",
            "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark, username, blurry",
        )
        self.prompt_file = Path(os.getenv("KLEE_PROMPT_FILE", str(DEFAULT_PROMPT_FILE)))
        self._client = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _clean_base_url(value: str | None) -> str | None:
        value = (value or "").strip()
        if not value:
            os.environ.pop("OPENAI_BASE_URL", None)
            return None
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            print(
                "OPENAI_BASE_URL ignored: it must start with http:// or https://",
                flush=True,
            )
            os.environ.pop("OPENAI_BASE_URL", None)
            return None
        return value.rstrip("/")

    def config_summary(self) -> str:
        base = "set" if self.base_url else "default"
        key = "set" if self.api_key else "missing"
        novelai = "set" if self.novelai_token else "missing"
        return (
            f"ai key={key} model={self.model} base_url={base} timeout={self.timeout:g} "
            f"image_provider={self.image_provider} novelai_token={novelai}"
        )

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI

            kwargs = {"api_key": self.api_key, "timeout": self.timeout}
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
            print(
                f"klee ai reply failed: {type(exc).__name__}: {exc}; {self.config_summary()}",
                flush=True,
            )
            cause = getattr(exc, "__cause__", None)
            if cause:
                print(f"klee ai reply cause: {type(cause).__name__}: {cause}", flush=True)
            return FALLBACK_REPLY

        if not text:
            return FALLBACK_REPLY
        return text[:1800]

    async def generate_image(self, *, prompt: str) -> bytes:
        if self.image_provider == "novelai":
            return await self._generate_novelai_image(prompt=prompt)
        if self.image_provider != "openai":
            raise RuntimeError("IMAGE_PROVIDER must be openai or novelai.")
        if not self.enabled:
            raise RuntimeError("OPENAI_API_KEY is missing.")
        response = await self._get_client().with_options(timeout=self.image_timeout).images.generate(
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

    async def _generate_novelai_image(self, *, prompt: str) -> bytes:
        if not self.novelai_token:
            raise RuntimeError("NOVELAI_TOKEN is missing.")
        payload = {
            "input": prompt,
            "model": self.novelai_model,
            "action": "generate",
            "parameters": {
                "width": self.novelai_width,
                "height": self.novelai_height,
                "scale": self.novelai_scale,
                "sampler": self.novelai_sampler,
                "steps": self.novelai_steps,
                "n_samples": 1,
                "seed": random.randint(0, 2**32 - 1),
                "uc": self.novelai_negative_prompt,
                "qualityToggle": True,
                "sm": False,
                "sm_dyn": False,
                "dynamic_thresholding": False,
                "controlnet_strength": 1,
                "legacy": False,
                "add_original_image": False,
                "cfg_rescale": 0,
                "noise_schedule": "karras",
                "params_version": 3,
            },
        }
        headers = {
            "Authorization": f"Bearer {self.novelai_token}",
            "Content-Type": "application/json",
            "Accept": "application/zip",
        }
        async with httpx.AsyncClient(timeout=self.image_timeout) as client:
            response = await client.post(
                "https://image.novelai.net/ai/generate-image",
                json=payload,
                headers=headers,
            )
        if response.status_code >= 400:
            detail = response.text.strip().replace("\n", " ")[:240]
            raise RuntimeError(f"NovelAI image generation failed: HTTP {response.status_code} {detail}")
        return self._first_image_from_zip(response.content)

    @staticmethod
    def _first_image_from_zip(content: bytes) -> bytes:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for name in archive.namelist():
                if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    return archive.read(name)
        raise RuntimeError("NovelAI returned no image file.")
