"""离线语音识别：把 QQ 语音（silk）或 wav 转成文字，用 sherpa-onnx + SenseVoice。

模型放在 /android-share/asr/（model.int8.onnx + tokens.txt），由手机本地推理，
不联网、不要 API Key。第一次调用才加载模型（约几百毫秒到几秒），之后复用。
"""

from __future__ import annotations

import asyncio
import base64
import shutil
import subprocess
import tempfile
import wave
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import unquote, urlsplit

import aiohttp
import numpy as np
import pilk
from numpy.typing import NDArray

_RATE = 16000
_SUFFIXES = {
    "x-wav": ".wav",
    "wave": ".wav",
    "vnd.wave": ".wav",
    "mpeg": ".mp3",
    "mp4": ".m4a",
    "x-m4a": ".m4a",
    "x-silk": ".silk",
    "silk": ".silk",
    "x-amr": ".amr",
}


@cache
def _sherpa() -> ModuleType | None:
    try:
        import sherpa_onnx
    except ImportError:
        return None
    return sherpa_onnx


async def _read_bytes(ref: str) -> tuple[bytes, str]:
    media_type = ""
    suffix = ""
    if ref.startswith("data:"):
        header, encoded = ref[5:].split(",", 1)
        media_type, *parameters = header.lower().split(";")
        if not media_type.startswith("audio/") or "base64" not in parameters:
            raise ValueError("invalid_audio_data_url")
        data = base64.b64decode(unquote(encoded), validate=True)
    elif (url := urlsplit(ref)).scheme in {"http", "https"}:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20)
        ) as session:
            async with session.get(ref) as response:
                response.raise_for_status()
                data, media_type = await response.read(), response.content_type
                suffix = Path(unquote(response.url.path)).suffix.lower()
    else:
        path = Path(ref)
        data, suffix = await asyncio.to_thread(path.read_bytes), path.suffix.lower()

    # 按文件头修正后缀：QQ 语音不带 .silk 扩展名时也能认出来
    if data.startswith((b"#!SILK_V3", b"\x02#!SILK_V3")):
        return data, ".silk"
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return data, ".wav"
    subtype = (
        media_type.removeprefix("audio/") if media_type.startswith("audio/") else ""
    )
    return data, _SUFFIXES.get(
        subtype, suffix or (f".{subtype}" if subtype.isalnum() else ".bin")
    )


def _samples(data: bytes, suffix: str) -> NDArray[np.float32]:
    temp = tempfile.TemporaryDirectory()
    try:
        folder = Path(temp.name)
        src, wav = folder / f"input{suffix}", folder / "converted.wav"
        src.write_bytes(data)
        if suffix == ".silk":
            pilk.silk_to_wav(str(src), str(wav))
        elif suffix == ".wav":
            wav = src
        elif (ffmpeg := shutil.which("ffmpeg")) is not None:
            subprocess.run(
                [
                    ffmpeg, "-nostdin", "-loglevel", "error", "-i", str(src),
                    "-ar", str(_RATE), "-ac", "1", "-f", "wav", str(wav),
                ],
                check=True,
                capture_output=True,
            )
        else:
            raise RuntimeError(f"unsupported_audio_format:{suffix}")

        with wave.open(str(wav), "rb") as reader:
            width = reader.getsampwidth()
            channels = reader.getnchannels()
            rate = reader.getframerate()
            pcm = reader.readframes(reader.getnframes())
        if width == 1:
            values = np.frombuffer(pcm, dtype=np.uint8).astype(np.int32) - 128
        elif width == 3:
            b = np.frombuffer(pcm, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
            values = (b[:, 0] | (b[:, 1] << 8) | ((b[:, 2] ^ 128) << 16)) - (1 << 23)
        else:
            values = np.frombuffer(pcm, dtype=f"<i{width}")
        mono = values.reshape(-1, channels).mean(axis=1) / (1 << (8 * width - 1))
        if rate != _RATE and mono.size:
            positions = np.arange(max(1, round(mono.size * _RATE / rate))) * (
                rate / _RATE
            )
            mono = np.interp(positions, np.arange(mono.size), mono)
        return mono.astype(np.float32)
    finally:
        temp.cleanup()


class LocalAsr:
    def __init__(
        self, config: dict, logger: Any, model_dir: str = "/android-share/asr"
    ) -> None:
        self.config = config
        self.logger = logger
        folder = Path(str(config.get("voice_asr_dir") or model_dir))
        self._model = folder / "model.int8.onnx"
        self._tokens = folder / "tokens.txt"
        self._lock = asyncio.Lock()
        self._loading: asyncio.Task[Any] | None = None

    def ready(self) -> bool:
        return _sherpa() is not None and self._model.is_file() and self._tokens.is_file()

    def _load(self) -> Any:
        sherpa = _sherpa()
        if sherpa is None:
            raise ImportError("sherpa_onnx")
        return sherpa.OfflineRecognizer.from_sense_voice(
            model=str(self._model),
            tokens=str(self._tokens),
            num_threads=2,
            use_itn=True,
            language="auto",
        )

    async def transcribe(self, ref: str) -> str:
        data, suffix = await _read_bytes(ref)
        async with self._lock:
            task = self._loading
            if task is None or (
                task.done() and (task.cancelled() or task.exception() is not None)
            ):
                task = self._loading = asyncio.create_task(
                    asyncio.to_thread(self._load)
                )
        recognizer = await asyncio.shield(task)

        def infer() -> str:
            samples = _samples(data, suffix)
            stream = recognizer.create_stream()
            stream.accept_waveform(_RATE, samples)
            recognizer.decode_stream(stream)
            return str(stream.result.text).strip()

        text = await asyncio.to_thread(infer)
        if not text:
            raise ValueError("empty_transcription")
        return text
