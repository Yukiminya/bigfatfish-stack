"""大肥鱼的嗓子：把回复念成语音发出去（手机本地 TTS，离线）。

引擎是 sherpa-onnx + VITS（fanchen-C 女声），模型放 /android-share/tts。
发 QQ 语音要 silk，用 pilk 现编；编不出来就退回发 wav。
"""

import asyncio
import hashlib
import json
import os
import random
import re
from contextlib import suppress
from pathlib import Path
from typing import Any

import astrbot.core.message.components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star, register

try:
    from astrbot.core.message.message_event_result import MessageChain
except ImportError:  # pragma: no cover
    from astrbot.api.event import MessageChain  # type: ignore[no-redef]

_DATA_DIR = "/persistent/astrbot/data/plugin_data/astrbot_plugin_feiyu_voice"

# 远程嗓子链：电脑上的 GPT-SoVITS（B=adb 反向隧道，A=局域网中转，C=云端，之后才用手机本地 VITS）
_DEFAULT_REMOTE = "http://127.0.0.1:9880,http://192.168.1.2:9881"


def _load_remote_module():
    """按文件路径装载同目录的 remote_tts.py（插件是单文件装载，相对 import 不稳）。"""
    import importlib.util

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "remote_tts.py")
    if not os.path.isfile(path):
        return None
    spec = importlib.util.spec_from_file_location("feiyu_remote_tts", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
_EMOJI = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f0ff"
    "\U0000fe00-\U0000fe0f\U0000200d]+"
)
_URL = re.compile(r"https?://\S+")


def _clean(text: str) -> str:
    """去掉代码块、markdown 记号、链接和 emoji，只留给嘴念的字。"""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`[^`]*`", " ", text)
    text = _URL.sub(" ", text)
    text = _EMOJI.sub("", text)
    text = re.sub(r"[*_~#>|\[\]{}<>]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


@register(
    "astrbot_plugin_feiyu_voice",
    "Codex",
    "给大肥鱼一副嗓子：把回复念成语音发出去",
    "v1.0.0",
)
class FeiyuVoice(Star):
    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context)
        settings = dict(config) if config is not None else {}
        self.voice_dir = str(settings.get("voice_dir") or "/android-share/tts")
        self.speed = float(settings.get("speed") or 1.0)
        try:
            self.probability = min(1.0, max(0.0, float(settings.get("probability", 0.35))))
        except (TypeError, ValueError):
            self.probability = 0.35
        try:
            self.max_chars = int(settings.get("max_chars", 80))
        except (TypeError, ValueError):
            self.max_chars = 80
        self.enabled = bool(settings.get("enabled", True))
        # 白名单：只有这些 QQ 号能拿到语音条（空列表 = 谁都可以）
        raw_whitelist = settings.get("voice_whitelist")
        if raw_whitelist is None:
            raw_whitelist = "641961016"
        if isinstance(raw_whitelist, (list, tuple, set)):
            items = [str(x) for x in raw_whitelist]
        else:
            items = str(raw_whitelist).replace("，", ",").split(",")
        self.voice_whitelist = {x.strip() for x in items if x.strip()}
        # 声线：voice_name 是 voice_dir 下的子目录名（空字符串表示直接用 voice_dir，即 VITS）
        self.voice_name = str(settings.get("voice_name") or "")
        try:
            self.sid = int(settings.get("sid", 0))
        except (TypeError, ValueError):
            self.sid = 0
        try:
            self.threads = max(1, int(settings.get("threads", 4)))
        except (TypeError, ValueError):
            self.threads = 4
        # 远程链：逗号分隔，按顺序试；全失败才用手机本地 VITS
        raw_remote = settings.get("remote_urls")
        if raw_remote is None:
            raw_remote = _DEFAULT_REMOTE
        self.remote_urls = [u.strip() for u in str(raw_remote).replace("|", ",").split(",") if u.strip()]
        self._remote = _load_remote_module()

        base = Path(_DATA_DIR)
        try:
            base.mkdir(parents=True, exist_ok=True)
            self.cache_dir = base
        except OSError:
            self.cache_dir = Path(__file__).resolve().parent
        self._state_path = self.cache_dir / "state.json"
        self._load_state()

        self._tts: Any = None
        self._lock = asyncio.Lock()

        with suppress(RuntimeError):
            asyncio.get_running_loop().create_task(self._maybe_greet())

    async def _maybe_greet(self) -> None:
        """自检用：cache 目录里放了 greet_once.txt（内容是 umo）就先发一条语音，结果写 greet_result.txt。"""
        flag = self.cache_dir / "greet_once.txt"
        if not flag.exists():
            return
        await asyncio.sleep(8)
        report = self.cache_dir / "greet_result.txt"
        try:
            raw = flag.read_text(encoding="utf-8").strip()
            umo, _, fixed = raw.partition("|")
            umo = umo.strip() or "napcat:FriendMessage:641961016"
            flag.unlink(missing_ok=True)
            if fixed.strip():
                path = fixed.strip()
            else:
                path = await self._voice_path("你好呀，我是大肥鱼，嗓子装好了，你听听还行吗。")
            if not path:
                raise RuntimeError("合成失败")
            ok = await self.context.send_message(
                umo, MessageChain([Comp.Record.fromFileSystem(path)])
            )
            report.write_text(f"send={ok} path={path}", encoding="utf-8")
            logger.info(f"嗓子：自检语音已发送 send={ok} {path}")
        except BaseException as exc:  # noqa: BLE001
            try:
                import traceback

                report.write_text(
                    f"error={exc!r}\n{traceback.format_exc()}", encoding="utf-8"
                )
            except OSError:
                pass
            logger.warning(f"嗓子：自检发送失败 {exc!r}")

    # ---------- 开关与参数 ----------

    def _load_state(self) -> None:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        if isinstance(data.get("enabled"), bool):
            self.enabled = data["enabled"]
        if isinstance(data.get("voice_name"), str):
            self.voice_name = data["voice_name"]
        if isinstance(data.get("sid"), int):
            self.sid = data["sid"]
        raw_remote = data.get("remote_urls")
        if isinstance(raw_remote, str) and raw_remote.strip():
            self.remote_urls = [u.strip() for u in raw_remote.replace("|", ",").split(",") if u.strip()]
        for key, caster in (("probability", float), ("speed", float), ("max_chars", int)):
            value = data.get(key)
            if isinstance(value, (int, float)):
                try:
                    setattr(self, key, caster(value))
                except (TypeError, ValueError):
                    pass

    def _save_state(self) -> None:
        payload = {
            "enabled": self.enabled,
            "probability": self.probability,
            "speed": self.speed,
            "max_chars": self.max_chars,
            "voice_name": self.voice_name,
            "sid": self.sid,
            "remote_urls": ",".join(self.remote_urls),
        }
        try:
            self._state_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning(f"嗓子：写状态失败 {exc!r}")

    # ---------- 合成 ----------

    def _voice_list(self) -> list[str]:
        """voice_dir 下每个带 onnx 的子目录就是一个可选声线。"""
        base = Path(self.voice_dir)
        try:
            return sorted(
                p.name for p in base.iterdir() if p.is_dir() and any(p.glob("*.onnx"))
            )
        except OSError:
            return []

    def _active_folder(self) -> Path:
        base = Path(self.voice_dir)
        if self.voice_name:
            candidate = base / self.voice_name
            if candidate.is_dir():
                return candidate
        return base

    def _build(self) -> Any:
        """建 OfflineTts（约 1 秒）。放线程里调。"""
        import sherpa_onnx as s

        folder = self._active_folder()
        onnx = sorted(folder.glob("*.onnx"))
        if not onnx:
            raise RuntimeError(f"{folder} 里没有 onnx 模型")
        rules = ",".join(str(p) for p in sorted(folder.glob("*.fst")))
        voices_bin = folder / "voices.bin"
        if voices_bin.is_file():
            lexicons = ",".join(
                str(p) for p in sorted(folder.glob("lexicon*.txt"))
            )
            model_cfg = s.OfflineTtsModelConfig(
                kokoro=s.OfflineTtsKokoroModelConfig(
                    model=str(onnx[0]),
                    voices=str(voices_bin),
                    tokens=str(folder / "tokens.txt"),
                    lexicon=lexicons,
                ),
                num_threads=self.threads,
            )
        else:
            model_cfg = s.OfflineTtsModelConfig(
                vits=s.OfflineTtsVitsModelConfig(
                    model=str(onnx[0]),
                    lexicon=str(folder / "lexicon.txt"),
                    tokens=str(folder / "tokens.txt"),
                ),
                num_threads=self.threads,
            )
        cfg = s.OfflineTtsConfig(
            model=model_cfg, rule_fsts=rules, max_num_sentences=1
        )
        return s.OfflineTts(cfg)

    def _synthesize(self, text: str, wav_path: Path) -> None:
        import numpy as np
        import wave

        if self._tts is None:
            self._tts = self._build()
        try:
            audio = self._tts.generate(text, sid=self.sid, speed=self.speed)
        except Exception:
            # VITS 只有 0 号说话人
            audio = self._tts.generate(text, sid=0, speed=self.speed)
        if not audio.samples:
            raise RuntimeError("合成结果为空")
        pcm = (np.array(audio.samples) * 32767).astype(np.int16)
        with wave.open(str(wav_path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(audio.sample_rate)
            out.writeframes(pcm.tobytes())

    def _to_silk(self, wav_path: Path, silk_path: Path) -> None:
        import wave

        import pilk

        with wave.open(str(wav_path), "rb") as reader:
            rate = reader.getframerate()
        # pcm_rate 必须等于 wav 自己的采样率，否则时长会错（16k 的 wav 传 24000 会变成 4 秒）
        try:
            pilk.encode(str(wav_path), str(silk_path), pcm_rate=rate)
        except Exception as exc:  # noqa: BLE001
            # pilk 写完文件后还会抛 SilkError(-11)（缓冲未落盘时看大小是 0），
            # 所以这里只记一笔，能不能用交给调用方按产物大小判断
            logger.warning(f"嗓子：pilk 抛错，稍后按产物判断 {exc!r}")

    async def _voice_path(self, text: str) -> str | None:
        """合成并返回可发送的音频路径；失败返回 None。"""
        cleaned = _clean(text)
        if not cleaned:
            return None
        digest = hashlib.md5(
            f"{self.voice_name}|{self.sid}|{cleaned}|{self.speed}".encode()
        ).hexdigest()
        wav = self.cache_dir / f"{digest}.wav"
        if wav.is_file() and wav.stat().st_size > 0:
            return str(wav)

        # 1) 电脑上的 GPT-SoVITS：B=adb 反向 → A=局域网 → C=云端
        if self.remote_urls and self._remote is not None:
            try:
                endpoints = [
                    {"name": f"r{i}", "url": u} for i, u in enumerate(self.remote_urls)
                ]
                data = await self._remote.synthesize_chain(cleaned, endpoints)
                if data and self._remote.wav_ok(data):
                    wav.write_bytes(data)
                    logger.info(f"嗓子：远程合成成功 {len(data)} 字节")
                    return str(wav)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"嗓子：远程链异常 {exc!r}")

        # 2) 兜底：手机本地 sherpa-onnx VITS
        try:
            async with self._lock:
                if not wav.is_file():
                    await asyncio.to_thread(self._synthesize, cleaned, wav)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"嗓子：合成失败 {exc!r}")
            return None
        # 直接发 wav：AstrBot 发 Record 前会自己把它转成 base64，
        # 送 silk 反而会触发它的解码器（pysilk）报 DEC_PAYLOAD_TOO_LARGE
        return str(wav) if wav.is_file() else None

    # ---------- 钩子：给回复加语音 ----------

    def _allowed(self, event) -> bool:
        """白名单外的人不给语音（私聊、群聊都按发送者 QQ 号算）。"""
        if not self.voice_whitelist:
            return True
        try:
            return str(event.get_sender_id()) in self.voice_whitelist
        except Exception:  # noqa: BLE001
            return False

    @filter.on_decorating_result()
    async def _attach_voice(self, event: AstrMessageEvent) -> None:
        try:
            if not self.enabled or event.get_extra("feiyu_voice_done"):
                return
            if not self._allowed(event):
                return
            result = event.get_result()
            chain = getattr(result, "chain", None)
            if not chain or any(isinstance(c, Comp.Record) for c in chain):
                return
            text = "".join(c.text for c in chain if isinstance(c, Comp.Plain))
            text = text.strip()
            if not text or (self.max_chars > 0 and len(text) > self.max_chars):
                return
            if random.random() > self.probability:
                return
            path = await self._voice_path(text)
            if path:
                chain.append(Comp.Record.fromFileSystem(path))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"嗓子：挂语音失败 {exc!r}")

    # ---------- 命令 ----------

    @filter.command("说")
    async def say(self, event: AstrMessageEvent, arg: str = ""):
        if not self._allowed(event):
            yield event.plain_result("这个只给主人用")
            return
        text = (arg or "").strip()
        if not text:
            match = re.fullmatch(r"/?说\s+(.+)", event.message_str.strip(), re.S)
            text = match.group(1).strip() if match else ""
        if not text:
            yield event.plain_result("用法：/说 <要念的文本>")
            return
        event.set_extra("feiyu_voice_done", True)
        path = await self._voice_path(text)
        if not path:
            yield event.plain_result("嗓子没出声，后台日志里有原因")
            return
        result = MessageEventResult()
        result.chain = [Comp.Record.fromFileSystem(path)]
        yield result

    @filter.command("试听")
    async def preview(self, event: AstrMessageEvent):
        if not self._allowed(event):
            yield event.plain_result("这个只给主人用")
            return
        event.set_extra("feiyu_voice_done", True)
        path = await self._voice_path("我是大肥鱼，今天也要好好吃饭哦。")
        if not path:
            yield event.plain_result("合成失败了，后台日志里有原因")
            return
        result = MessageEventResult()
        result.chain = [Comp.Record.fromFileSystem(path)]
        yield result

    @filter.command("嗓子")
    @filter.command("语音")
    async def voice_ctl(self, event: AstrMessageEvent, arg: str = ""):
        if not self._allowed(event):
            yield event.plain_result("这个只给主人用")
            return
        raw = event.message_str.strip()
        # /语音 不带参数就是开关；/嗓子 不带参数照旧只看状态
        is_alias = re.fullmatch(r"/?语音", raw) is not None
        text = (arg or "").strip()
        if not text:
            match = re.fullmatch(r"/?(?:嗓子|语音)(?:\s+(.+))?", raw, re.S)
            text = (match.group(1) or "").strip() if match else ""
        message = ""
        try:
            if is_alias and not text:
                self.enabled = not self.enabled
                message = "语音条已开" if self.enabled else "语音条已关"
            elif text in ("开", "关"):
                self.enabled = text == "开"
                message = "嗓子已开" if self.enabled else "嗓子已关"
            elif text.startswith("声线"):
                parts = text.split()
                if len(parts) == 1:
                    names = self._voice_list()
                    message = "可用声线：" + ("、".join(names) if names else "（没有子目录）")
                    message += f"\n当前：{self.voice_name or 'vits'} · sid={self.sid}"
                else:
                    name = parts[1]
                    self.voice_name = "" if name in ("vits", "基础") else name
                    if len(parts) > 2 and parts[2].lstrip("-").isdigit():
                        self.sid = int(parts[2])
                    self._tts = None
                    message = f"声线切到 {self.voice_name or 'vits'} · sid={self.sid}"
            elif match := re.fullmatch(r"概率\s*([0-9.]+)", text):
                self.probability = min(1.0, max(0.0, float(match.group(1))))
                message = f"附带语音的概率：{self.probability}"
            elif match := re.fullmatch(r"速度\s*([0-9.]+)", text):
                self.speed = min(2.0, max(0.5, float(match.group(1))))
                message = f"语速：{self.speed}"
            elif match := re.fullmatch(r"字数\s*(\d+)", text):
                self.max_chars = int(match.group(1))
                message = f"超过 {self.max_chars} 字就不念"
            self._save_state()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"嗓子：改参数失败 {exc!r}")
            message = "参数没改成功"
        status = (
            f"{'开' if self.enabled else '关'} · 概率 {self.probability:.2f}"
            f" · 语速 {self.speed:.2f} · 超过 {self.max_chars} 字不念"
        )
        yield event.plain_result(f"{message}\n{status}" if message else status)
