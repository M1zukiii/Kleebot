import base64
import os
import random
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .chat_model import ChatModel, IncompleteReply
from .model_budget import BASIC_MESSAGE
from .diagnostics import DiagnosticStore, explanation, scrub


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PROMPT_FILE = BASE_DIR / "klee_prompt.txt"

KLEE_SYSTEM_PROMPT = """你是 Discord 机器人 Kleebot。回复时以 Klee 的人设说话。
自称 Klee，语气活泼、天真、热情，回复短一点。不要说自己是 AI、语言模型或 API。
遇到危险、违法、现实伤害内容时，用 Klee 的语气拒绝或转移话题。"""

FALLBACK_REPLY = "西风骑士团，「火花骑士」，Klee，前来报到！…呃—后面该说什么词来着？Klee背不下来啦..."

NOVELAI_ASPECT_SIZES = {
    "square": (1024, 1024),
    "portrait": (832, 1216),
    "landscape": (1216, 832),
}
OPENAI_IMAGE_SIZES = {
    "square": "1024x1024",
    "portrait": "1024x1536",
    "landscape": "1536x1024",
}


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    prompt: str


class KleeAI:
    def __init__(self) -> None:
        self.chat_model = ChatModel()
        self.diagnostics = DiagnosticStore.from_env()
        self.web_search = os.getenv("KLEE_WEB_SEARCH", "true").strip().lower() in {"1", "true", "yes", "on"} and self.chat_model.config.provider == "openai"
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.model = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
        self.image_model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1.5")
        self.image_size = os.getenv("OPENAI_IMAGE_SIZE", "1024x1024")
        self.timeout = float(os.getenv("OPENAI_TIMEOUT", "60"))
        self.image_timeout = float(os.getenv("OPENAI_IMAGE_TIMEOUT", "180"))
        self.base_url = self._clean_base_url(os.getenv("OPENAI_BASE_URL"))
        self.image_provider = os.getenv("IMAGE_PROVIDER", "openai").strip().lower()
        self.novelai_token = os.getenv("NOVELAI_TOKEN")
        self.novelai_model = os.getenv("NOVELAI_MODEL", "nai-diffusion-5-full")
        self.novelai_width = int(os.getenv("NOVELAI_WIDTH", "1024"))
        self.novelai_height = int(os.getenv("NOVELAI_HEIGHT", "1024"))
        self.novelai_steps = int(os.getenv("NOVELAI_STEPS", "28"))
        self.novelai_scale = float(os.getenv("NOVELAI_SCALE", "6.0"))
        self.novelai_sampler = os.getenv("NOVELAI_SAMPLER", "k_euler_ancestral")
        self.novelai_avoid_anlas = os.getenv("NOVELAI_AVOID_ANLAS", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.novelai_negative_prompt = os.getenv(
            "NOVELAI_NEGATIVE_PROMPT",
            "lowres, artistic error, film grain, scan artifacts, worst quality, bad quality, jpeg artifacts, very displeasing, chromatic aberration, dithering, halftone, screentone, multiple views, logo, too many watermarks, negative space, blank page, @_@, mismatched pupils, glowing eyes, bad anatomy",
        )
        self.novelai_plain_prompt = os.getenv("NOVELAI_PLAIN_PROMPT", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.novelai_prompt_model = os.getenv("NOVELAI_PROMPT_MODEL") or self.chat_model.config.model
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
            f"chat provider={self.chat_model.config.provider} model={self.chat_model.config.model} key={'set' if self.chat_model.enabled else 'missing'}; image ai key={key} model={self.model} base_url={base} timeout={self.timeout:g} "
            f"image_provider={self.image_provider} novelai_model={self.novelai_model} "
            f"novelai_token={novelai} avoid_anlas={self.novelai_avoid_anlas}"
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
        diagnostic_scope=None,
    ) -> str:
        if not self.chat_model.enabled:
            if self.chat_model.budget.state()[0]:
                return BASIC_MESSAGE
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
        text_prompt += (
            '\n讲解题目时可以适当展开步骤，不能为了缩短回复而省略题目条件和关键推导。'
            '\nDiscord 不直接显示 LaTeX。简单公式优先写 Unicode 纯文字，例如 x² + y² = r²；'
            '复杂分式、根式、积分、求和使用 \\[...\\] 包裹，每条公式单独一个块，'
            '程序会把支持的公式转为图片。使用标准数学命令，不把中文解释放入公式；'
            '不使用完整 LaTeX 文档、导入宏包或自定义宏。解释文字放在公式块之外。'
        )

        try:
            text = await self.chat_model.generate(
                instructions=self._system_prompt() + '\n当前这条回复是普通聊天模式，没有在这条回复中执行群管理工具。Klee 已接入管理员任务执行模块，管理员明确委派时可以管理频道、禁言、安排活动和播放音乐。不得声称已经执行这些操作，也不要笼统声称 Klee 没有工具或不能加入语音。具体失败原因、权限和语音状态必须根据真实执行结果；缺少结果时直接说明暂时无法确认，不要猜测。', text=text_prompt,
                image_urls=image_urls, max_tokens=2400 if self.chat_model.config.provider == 'openai' else 900,
                web_search=self.web_search,
            )
        except Exception as exc:
            if self.chat_model.budget.state()[0]:
                return BASIC_MESSAGE
            incident = self.diagnostics.record('聊天/联网',exc,**(diagnostic_scope or {}))
            print(
                f"klee ai reply failed: {type(exc).__name__}: {scrub(exc)}; incident={incident}",
                flush=True,
            )
            cause = getattr(exc, "__cause__", None)
            if cause:
                print(f"klee ai reply cause: {type(cause).__name__}: {scrub(cause)}", flush=True)
            reference = f'（诊断编号：{incident}）'
            if isinstance(exc, IncompleteReply):
                return "唔，Klee 这次的回答没能生成完整。可以把题目分成几个小问，Klee 一步一步讲给你听。"+reference
            if type(exc).__name__ in {'APITimeoutError', 'TimeoutError'}:
                return "唔，这次等回复超时了，请稍后再试一次。"+reference
            return '唔，Klee 这次没能完成回复。'+explanation(exc)+reference

        if not text:
            return FALLBACK_REPLY
        return text[:1800]

    async def generate_image(self, *, prompt: str, plain_language: bool = True, aspect: str | None = None) -> bytes:
        return (
            await self.generate_image_with_prompt(
                prompt=prompt,
                plain_language=plain_language,
                aspect=aspect,
            )
        ).data

    async def generate_image_with_prompt(
        self,
        *,
        prompt: str,
        plain_language: bool = True,
        aspect: str | None = None,
        negative_prompt: str | None = None,
    ) -> GeneratedImage:
        self.chat_model.budget.before_request()
        if self.image_provider == "novelai":
            return await self._generate_novelai_image(
                prompt=prompt,
                plain_language=plain_language,
                aspect=aspect,
                negative_prompt=negative_prompt,
            )
        if self.image_provider != "openai":
            raise RuntimeError("IMAGE_PROVIDER must be openai or novelai.")
        if not self.enabled:
            raise RuntimeError("OPENAI_API_KEY is missing.")
        image_size = OPENAI_IMAGE_SIZES.get(self._normalize_aspect(aspect), self.image_size)
        response = await self._get_client().with_options(timeout=self.image_timeout).images.generate(
            model=self.image_model,
            prompt=prompt,
            size=image_size,
            n=1,
        )
        if not response.data:
            raise RuntimeError("Image generation returned no data.")

        item = response.data[0]
        b64_json = getattr(item, "b64_json", None)
        if b64_json:
            return GeneratedImage(data=base64.b64decode(b64_json), prompt=prompt)

        image_url = getattr(item, "url", None)
        if image_url:
            from urllib.request import urlopen

            with urlopen(image_url, timeout=30) as result:
                return GeneratedImage(data=result.read(), prompt=prompt)

        raise RuntimeError("Image generation did not return image bytes.")

    async def novelai_prompt_text(self, *, prompt: str, aspect: str | None = None) -> str:
        if not prompt.strip():
            raise RuntimeError("Prompt is empty.")
        return await self._novelai_prompt(prompt, aspect=aspect)

    async def _generate_novelai_image(
        self,
        *,
        prompt: str,
        plain_language: bool,
        aspect: str | None,
        negative_prompt: str | None,
    ) -> GeneratedImage:
        if not self.novelai_token:
            raise RuntimeError("NOVELAI_TOKEN is missing.")
        if plain_language:
            prompt = await self._novelai_prompt(prompt, aspect=aspect)
        try:
            from novelai_python import ApiCredential, GenerateImageInfer
            from novelai_python.sdk.ai.generate_image import Sampler, UCPreset
            from loguru import logger
            from pydantic import SecretStr
        except ImportError as exc:
            raise RuntimeError("novelai-python is not installed.") from exc

        logger.disable("novelai_python")
        sampler = self._novelai_enum_value(Sampler, self.novelai_sampler, Sampler.K_EULER_ANCESTRAL)
        width, height, steps = self._novelai_generation_settings(aspect)
        builder_model = self._novelai_builder_model()
        request = GenerateImageInfer.build_generate(
            prompt=prompt,
            model=builder_model,
            negative_prompt=self.novelai_negative_prompt if negative_prompt is None else negative_prompt,
            ucPreset=UCPreset.TYPE0,
            steps=steps,
            sampler=sampler,
            width=width,
            height=height,
            seed=random.randint(1, 4294967295 - 7),
            qualityToggle=True,
            decrisp_mode=False,
            variety_boost=True,
            furry_mode=False,
        )
        request.parameters.scale = self.novelai_scale
        if builder_model != self.novelai_model:
            request.model = self.novelai_model
        session = ApiCredential(api_token=SecretStr(self.novelai_token.strip()))
        try:
            response = await request.request(session=session)
        except Exception as exc:
            detail = self._novelai_error_detail(exc)
            raise RuntimeError(f"NovelAI image generation failed: {detail}") from exc
        if not response.files:
            raise RuntimeError("NovelAI returned no image file.")
        return GeneratedImage(data=response.files[0][1], prompt=prompt)

    async def novelai_usage_text(self) -> str:
        if not self.novelai_token:
            return "NOVELAI_TOKEN 还没设置。"
        try:
            payload = await self._novelai_get_json("https://api.novelai.net/user/subscription")
        except Exception as exc:
            return (
                "Klee 现在查不到 NovelAI 额度，可能是 NovelAI 的订阅接口不接受这个 API token。\n"
                f"当前设置：model={self.novelai_model}, size={self.novelai_width}x{self.novelai_height}, "
                f"steps={self.novelai_steps}, avoid_anlas={self.novelai_avoid_anlas}\n"
                f"查询失败：{type(exc).__name__}: {str(exc)[:160]}"
            )

        tier_names = {0: "Paper", 1: "Tablet", 2: "Scroll", 3: "Opus"}
        tier = payload.get("tier")
        training = payload.get("trainingStepsLeft") or {}
        anlas_left = int(training.get("fixedTrainingStepsLeft") or 0) + int(training.get("purchasedTrainingSteps") or 0)
        perks = payload.get("perks") or {}
        lines = [
            f"NovelAI: {tier_names.get(tier, 'Unknown')}，active={payload.get('active')}",
            f"Anlas: {anlas_left}",
            f"Unlimited image: {bool(perks.get('unlimitedImageGeneration'))}",
            f"当前模型: {self.novelai_model}",
            f"当前设置: {self.novelai_width}x{self.novelai_height}, steps={self.novelai_steps}, avoid_anlas={self.novelai_avoid_anlas}",
        ]
        if self.novelai_avoid_anlas:
            width, height, steps = self._novelai_generation_settings()
            lines.append(f"实际保护设置: {width}x{height}, steps={steps}, single image")
            lines.append("V5 在 Opus 且普通尺寸/28 steps 内会优先走 V5 usage limit；如果额度耗尽，NovelAI 可能改扣 Anlas。")
        return "\n".join(lines)

    async def _novelai_get_json(self, url: str) -> dict:
        import httpx

        headers = {"Authorization": f"Bearer {self.novelai_token.strip()}"}
        async with httpx.AsyncClient(timeout=min(self.image_timeout, 30)) as client:
            response = await client.get(url, headers=headers)
        if response.status_code >= 400:
            detail = response.text.strip().replace("\n", " ")[:240]
            raise RuntimeError(f"HTTP {response.status_code} {detail}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("NovelAI returned an unexpected response.")
        return payload

    def _novelai_generation_settings(self, aspect: str | None = None) -> tuple[int, int, int]:
        aspect_size = NOVELAI_ASPECT_SIZES.get(self._normalize_aspect(aspect))
        if aspect_size:
            width, height = aspect_size
        else:
            width = self._multiple_of_64(self.novelai_width)
            height = self._multiple_of_64(self.novelai_height)
        steps = self.novelai_steps
        if self.novelai_avoid_anlas:
            steps = min(steps, 28)
        return width, height, steps

    @staticmethod
    def _normalize_aspect(aspect: str | None) -> str:
        value = (aspect or "").strip().lower()
        aliases = {
            "1:1": "square",
            "方图": "square",
            "方": "square",
            "square": "square",
            "portrait": "portrait",
            "vertical": "portrait",
            "竖图": "portrait",
            "竖": "portrait",
            "2:3": "portrait",
            "landscape": "landscape",
            "horizontal": "landscape",
            "横图": "landscape",
            "横": "landscape",
            "3:2": "landscape",
        }
        return aliases.get(value, value)

    def _novelai_builder_model(self) -> str:
        model = self.novelai_model.strip()
        if model in {"nai-diffusion-5-full", "nai-diffusion-5-curated"}:
            return "nai-diffusion-4-5-full" if model.endswith("-full") else "nai-diffusion-4-5-curated"
        return model

    @staticmethod
    def _multiple_of_64(value: int) -> int:
        return max(64, min(49152, (int(value) // 64) * 64))

    @staticmethod
    def _novelai_enum_value(enum_type, value: str, default):
        try:
            return enum_type(value)
        except ValueError:
            return default

    @staticmethod
    def _novelai_error_detail(exc: Exception) -> str:
        code = getattr(exc, "code", None)
        response = getattr(exc, "response", None)
        message = str(exc).strip() or type(exc).__name__
        if code and response:
            return f"HTTP {code} {str(response)[:240]}"
        if code:
            return f"HTTP {code} {message[:240]}"
        return f"{type(exc).__name__}: {message[:240]}"

    async def _novelai_prompt(self, prompt: str, aspect: str | None = None) -> str:
        prompt = prompt.strip()
        if not self.novelai_plain_prompt or not self.chat_model.enabled or self._looks_like_tag_prompt(prompt):
            return prompt
        aspect_name = self._normalize_aspect(aspect) or "square"
        aspect_notes = {
            "square": "Use a balanced square composition with a clear subject and readable background.",
            "portrait": "Use a vertical portrait composition, full body or upper body framing when appropriate.",
            "landscape": "Use a wide landscape composition with stronger environment and scene layout.",
        }
        instructions = (
            "You are a NovelAI Diffusion V5 Full prompt generator. "
            "Convert the user's plain-language image request into one strong positive prompt. "
            "Write only the final prompt, no explanation, no markdown, no labels. "
            "Use concise English comma-separated anime tags plus short natural-language details where tags are not enough. "
            "Start with subject count and main subject, then character identity, appearance, outfit, pose, expression, "
            "camera/framing, lighting, background, mood, and style/quality tags. "
            "Preserve the user's named characters, outfits, pose, mood, location, composition, and style. "
            "Do not invent extra characters unless the user asks for them. "
            "If the user writes Chinese or Japanese, translate naturally into English. "
            f"Composition rule for {aspect_name}: {aspect_notes.get(aspect_name, aspect_notes['square'])} "
            "Do not add sexual content, gore, hate, or real-person impersonation. "
            "Avoid overlong prompts; keep it under 900 characters."
        )
        try:
            rewritten = await self.chat_model.generate(
                model=self.novelai_prompt_model, instructions=instructions,
                text=prompt[:4000], max_tokens=220,
            )
        except Exception as exc:
            print(f"novelai prompt rewrite failed: {type(exc).__name__}: {exc}", flush=True)
            return prompt
        if not rewritten:
            return prompt
        return rewritten[:1800]

    @staticmethod
    def _looks_like_tag_prompt(prompt: str) -> bool:
        if re.search(r"[\u3400-\u9fff]", prompt):
            return False
        comma_count = prompt.count(",")
        word_count = len(prompt.split())
        return comma_count >= 3 and comma_count >= max(2, word_count // 5)
