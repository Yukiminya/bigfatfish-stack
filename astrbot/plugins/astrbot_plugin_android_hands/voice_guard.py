"""语音消息兜底：把 LLM 请求里听不了的 audio_urls 处理掉。

AstrBot 收到 QQ 语音（Record）时会把音频放进 ProviderRequest.audio_urls，
OpenAI 兼容适配器再把它转成 {"type": "input_audio"} 内容块；DeepSeek 不支持
音频输入，会直接回 422。这里在 on_llm_request 阶段把音频摘掉：能转写就转写，
不能转写就换成一段说明，让模型自己回一句"我听不了语音"。
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp

try:  # 本地识别是可选的：没装 sherpa-onnx 也不该拖垮插件加载
    from local_asr import LocalAsr
except Exception:  # noqa: BLE001
    LocalAsr = None  # type: ignore[assignment]

NOTICE = (
    "（用户发来一条语音消息，当前听不到语音内容。"
    "请用一句话自然说明你暂时听不了语音、请对方打字，不要编造语音内容。）"
)


def _audio_suffix(path: str, content_type: str = "") -> str:
    suffix = Path(path).suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        return suffix

    mime = content_type.partition(";")[0].strip().lower()
    common = {
        "audio/aac": ".aac",
        "audio/amr": ".amr",
        "audio/mp4": ".m4a",
        "audio/mpeg": ".mp3",
        "audio/ogg": ".ogg",
        "audio/silk": ".silk",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
    }
    return common.get(mime) or mimetypes.guess_extension(mime) or ".bin"


async def _read_audio(ref: str) -> tuple[bytes, str]:
    if ref.lower().startswith("data:"):
        header, separator, payload = ref[5:].partition(",")
        mime = header.partition(";")[0].lower()
        if not separator or not mime.startswith("audio/"):
            raise ValueError("invalid_audio_data_url")
        if not header.lower().endswith(";base64"):
            raise ValueError("audio_data_url_requires_base64")
        return base64.b64decode(payload, validate=True), _audio_suffix("", mime)

    if ref.lower().startswith(("http://", "https://")):
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(ref) as response:
                response.raise_for_status()
                mime = response.headers.get("Content-Type", "")
                path = urlsplit(str(response.url)).path
                return await response.read(), _audio_suffix(path, mime)

    path = Path(ref)
    return await asyncio.to_thread(path.read_bytes), _audio_suffix(ref)


class VoiceGuard:
    def __init__(self, config: dict, logger: Any) -> None:
        self.config = config
        self.logger = logger
        self.local = LocalAsr(config, logger) if LocalAsr is not None else None

    async def rewrite(self, req: Any, event: Any) -> str:
        if not req.audio_urls:
            return "no_audio"

        original = req.prompt or ""
        ref = req.audio_urls[0]
        req.audio_urls = []
        status = "dropped"

        if self.config.get("voice_asr_local") and self.local is not None:
            try:
                if not self.local.ready():
                    status = "asr_local_fail:not_ready"
                else:
                    text = await asyncio.wait_for(
                        self.local.transcribe(ref), timeout=120
                    )
                    prefix = original + "\n" if original else ""
                    req.prompt = prefix + "[语音转写] " + text
                    return f"asr_local_ok:{len(text)}"
            except Exception as exc:
                self.logger.warning(f"本地语音识别失败：{exc!r}")
                status = f"asr_local_fail:{type(exc).__name__}"

        try:
            base = str(self.config.get("voice_asr_base") or "").strip()
            key = str(self.config.get("voice_asr_key") or "").strip()
            model = str(self.config.get("voice_asr_model") or "").strip()
            if self.config.get("voice_asr_enabled") and base and key and model:
                text = await asyncio.wait_for(
                    self._transcribe(ref, base, key, model), timeout=20
                )
                prefix = original + "\n" if original else ""
                req.prompt = prefix + "[语音转写] " + text
                return f"asr_ok:{len(text)}"
        except Exception as exc:
            reason = type(exc).__name__
            if isinstance(exc, aiohttp.ClientResponseError):
                reason = f"HTTP {exc.status}"
            status = f"asr_fail:{reason[:120]}"

        req.prompt = NOTICE + ("\n" + original if original else "")
        return status

    async def _transcribe(
        self, ref: str, base: str, key: str, model: str
    ) -> str:
        audio, suffix = await _read_audio(ref)
        if not audio:
            raise ValueError("empty_audio")

        filename = "voice" + suffix
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        form = aiohttp.FormData()
        form.add_field("file", audio, filename=filename, content_type=mime)
        form.add_field("model", model)

        url = base.rstrip("/") + "/audio/transcriptions"
        headers = {"Authorization": f"Bearer {key}"}
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, data=form, headers=headers) as response:
                response.raise_for_status()
                result = await response.json(content_type=None)

        if isinstance(result, dict):
            text = result.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
            data = result.get("data")
            if isinstance(data, dict):
                text = data.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
        raise ValueError("missing_transcription_text")
