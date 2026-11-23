"""远程嗓子：把文字 POST 给电脑上的 GPT-SoVITS 语音接口，按顺序试到成功为止。

顺序由调用方给（B = adb 反向隧道 127.0.0.1:9880；A = 局域网中转 192.168.1.7:9881；
C = 云端）。全部失败返回 None，调用方再退回手机本地的 sherpa-onnx VITS。
C 通道写成 edge:zh-CN-XiaoyiNeural，冒号后是 Edge TTS 的音色名。
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_BYTES = 4000
PRECHECK_TIMEOUT = 2.0
POST_TIMEOUT = 60
DOWN_SECONDS = 120

_down_until: dict[str, float] = {}
_retrying: set[str] = set()


def health() -> dict[str, dict]:
    """返回有熔断记录的端点及剩余等待秒数。"""
    now = time.monotonic()
    return {
        url: {"down": until > now, "retry_in": round(max(0.0, until - now), 1)}
        for url, until in _down_until.copy().items()
    }


def _tcp_precheck(url: str) -> None:
    target = urllib.parse.urlsplit(url)
    if target.scheme not in ("http", "https"):
        return
    host = target.hostname
    if not host:
        raise ValueError("HTTP 端点缺少主机名")
    port = target.port
    if port is None:
        port = 443 if target.scheme == "https" else 80
    with socket.create_connection((host, port), timeout=PRECHECK_TIMEOUT):
        pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def wav_ok(data: bytes) -> bool:
    return bool(data) and data[:4] == b"RIFF"


def _post_tts(url: str, body: bytes) -> bytes:
    req = urllib.request.Request(
        f"{url.rstrip('/')}/tts", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=POST_TIMEOUT) as resp:
            status = getattr(resp, "status", 200)
            if status != 200:
                raise ValueError(f"HTTP {status}")
            audio = resp.read()
    except urllib.error.HTTPError as exc:
        exc.close()
        raise
    if len(audio) < MIN_BYTES:
        raise ValueError(f"音频只有 {len(audio)} 字节")
    if not wav_ok(audio):
        raise ValueError("返回的不是 WAV")
    return audio


def _mp3_to_wav(mp3: bytearray) -> bytes:
    with tempfile.TemporaryDirectory(prefix="feiyu-tts-") as directory:
        mp3_path = Path(directory) / "a.mp3"
        wav_path = Path(directory) / "a.wav"
        mp3_path.write_bytes(mp3)
        try:
            subprocess.run(
                [
                    "/usr/bin/ffmpeg", "-y", "-loglevel", "error",
                    "-i", str(mp3_path), "-ar", "24000", "-ac", "1", str(wav_path),
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"ffmpeg 转换失败（退出码 {exc.returncode}）：{detail}") from exc
        return wav_path.read_bytes()


async def _edge_tts(voice: str, text: str) -> bytes:
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("无法导入 edge_tts") from exc

    if not voice:
        raise ValueError("Edge TTS 音色名为空")

    mp3 = bytearray()
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            mp3.extend(chunk["data"])
    if not mp3:
        raise ValueError("Edge TTS 未返回音频")

    audio = await asyncio.to_thread(_mp3_to_wav, mp3)
    if len(audio) < MIN_BYTES:
        raise ValueError(f"音频只有 {len(audio)} 字节")
    if not wav_ok(audio):
        raise ValueError("返回的不是 WAV")
    return audio


async def synthesize_chain(text: str, endpoints: list[dict]) -> bytes | None:
    body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    for ep in endpoints:
        name = str(ep.get("name") or "?")
        url = str(ep.get("url") or "").strip()
        if not url or url in _retrying:
            continue

        until = _down_until.get(url)
        if until is not None and until > time.monotonic():
            continue
        if until is not None:
            # 到期后只让一个调用试探，其余调用继续尝试后面的端点。
            _down_until.pop(url, None)
            _retrying.add(url)
            logger.info("嗓子：远程 %s (%s) 熔断到期，重新尝试", name, url)

        is_edge = url.startswith("edge:")
        stage = "Edge TTS" if is_edge else "TCP 预检"
        try:
            if is_edge:
                audio = await _edge_tts(url.removeprefix("edge:").strip(), text)
            else:
                await asyncio.wait_for(
                    asyncio.to_thread(_tcp_precheck, url), timeout=PRECHECK_TIMEOUT,
                )
                stage = "POST"
                audio = await asyncio.wait_for(
                    asyncio.to_thread(_post_tts, url, body), timeout=POST_TIMEOUT,
                )
        except Exception as exc:
            now = time.monotonic()
            previous = _down_until.get(url)
            _down_until[url] = now + DOWN_SECONDS
            if previous is None or previous <= now:
                error_type = type(exc).__name__
                if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, Exception):
                    error_type += f"/{type(exc.reason).__name__}"
                logger.warning(
                    "嗓子：远程 %s (%s) %s 失败（%s：%r），熔断 %d 秒",
                    name, url, stage, error_type, exc, DOWN_SECONDS,
                )
        else:
            _down_until.pop(url, None)
            return audio
        finally:
            if until is not None:
                _retrying.discard(url)
    return None
