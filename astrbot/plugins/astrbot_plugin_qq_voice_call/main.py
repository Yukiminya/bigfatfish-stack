"""QQ 语音来电接听 + 实时语音对话。

链路：NapCat 语音桥（WS 客户端）→ 本插件（WS 服务端）→ 本地 sherpa ASR →
AstrBot 大模型 → 电脑上的 GPT-SoVITS → PulseAudio 虚拟声卡 → 回到 QQ 通话。

只接听，不拨号：桥接协议里没有拨号指令，插件也不实现。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import deque
from typing import Any

import numpy as np

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:  # AstrBot 的 logger 带颜色，退路是标准 logging
    from astrbot.api import logger as _astr_logger
except Exception:  # noqa: BLE001
    _astr_logger = None

LOG_PATH = "/android-share/qqcall.log"
RATE = 16000
FRAME_MS = 100
FRAME_SAMPLES = RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2
VOICE_RMS = 320.0
SILENCE_MS = 350
MIN_VOICE_MS = 400
MAX_UTTER_MS = 15000

_EMOJI = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f0ff"
    "\U0000fe00-\U0000fe0f\U0000200d]+"
)


def log(msg: str) -> None:
    """同时写文件与 AstrBot 日志，方便 adb 侧排查。"""
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    if _astr_logger is not None:
        _astr_logger.info(line)
    else:
        logging.getLogger("qqcall").info(line)


def strip_for_speech(text: str) -> str:
    """去掉 markdown、链接和 emoji，只留能念的字。"""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = _EMOJI.sub("", text)
    text = re.sub(r"[*_~#>|\[\]{}<>]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


async def run(cmd: list[str], timeout: float = 20.0) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, b"", b"timeout"
    return proc.returncode or 0, out, err


@register(
    "astrbot_plugin_qq_voice_call",
    "coo",
    "QQ 语音来电接听与实时语音对话（只接听，不拨号）",
    "v1.0.0",
)
class QQVoiceCall(Star):
    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context)
        cfg = dict(config) if config is not None else {}

        self.ws_host = str(cfg.get("ws_host") or "127.0.0.1")
        try:
            self.ws_port = int(cfg.get("ws_port") or 6198)
        except (TypeError, ValueError):
            self.ws_port = 6198
        self.ws_token = str(cfg.get("ws_token") or "coovoice2026")
        self.ws_path = str(cfg.get("ws_path") or "/qq-voice-call")

        raw = cfg.get("voice_whitelist")
        if raw is None:
            raw = "641961016"
        if isinstance(raw, (list, tuple, set)):
            items = [str(x) for x in raw]
        else:
            items = str(raw).replace("，", ",").split(",")
        self.whitelist = {x.strip() for x in items if x.strip()}

        # 有些来电只给 UID、解析不出 QQ 号；UID 也是账号的唯一标识，一并认。
        raw_uid = cfg.get("voice_uid_whitelist")
        if raw_uid is None:
            raw_uid = "u_hYiAbro406zZ6eH10MNN6Q"
        if isinstance(raw_uid, (list, tuple, set)):
            uid_items = [str(x) for x in raw_uid]
        else:
            uid_items = str(raw_uid).replace("，", ",").split(",")
        self.uid_whitelist = {x.strip() for x in uid_items if x.strip()}

        self.tts_url = str(cfg.get("tts_url") or "http://127.0.0.1:9880").rstrip("/")
        self.asr_model = str(cfg.get("asr_model") or "/android-share/asr/model.int8.onnx")
        self.asr_tokens = str(cfg.get("asr_tokens") or "/android-share/asr/tokens.txt")
        self.speaker_monitor = str(cfg.get("speaker_monitor") or "qqspk.monitor")
        self.mic_sink = str(cfg.get("mic_sink") or "qqmic")
        try:
            self.max_reply_chars = max(20, int(cfg.get("max_reply_chars") or 60))
        except (TypeError, ValueError):
            self.max_reply_chars = 60
        self.auto_accept = bool(cfg.get("auto_accept", True))

        self._ws_server: Any = None
        self._sock: Any = None
        self._recognizer: Any = None
        self._asr_lock = asyncio.Lock()
        self._capture: asyncio.subprocess.Process | None = None
        self._player: asyncio.subprocess.Process | None = None
        self._audio_task: asyncio.Task[Any] | None = None
        self._stop_audio = False
        self._in_call = False
        self._caller = ""
        self._phase = "idle"
        self._history: deque[tuple[str, str]] = deque(maxlen=8)
        self._last_error = ""
        self._accept_seq = 0
        self._av_state: tuple[bool, bool, bool] = (False, False, False)
        self._recover_task: asyncio.Task[Any] | None = None
        self._call_cutoff = 0

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._serve())
            loop.create_task(self._poll_status())
            loop.create_task(self._warm_asr())
        except RuntimeError:
            log("没有运行中的事件循环，WS 服务未启动")

    # ------------------------------------------------------------------ 语音设备

    async def _pulse_ready(self) -> bool:
        """确保 PulseAudio 与两路虚拟设备存在；失败返回 False。"""
        rc, _, _ = await run(["pactl", "info"], 8)
        if rc != 0:
            log("PulseAudio 未运行，尝试启动")
            await run(["pulseaudio", "-D", "--exit-idle-time=-1", "--disallow-exit"], 15)
            await asyncio.sleep(1.5)
            rc, _, _ = await run(["pactl", "info"], 8)
            if rc != 0:
                log("PulseAudio 启动失败")
                return False

        rc, sinks, _ = await run(["pactl", "list", "short", "sinks"], 8)
        names = {line.split("\t")[1] for line in sinks.decode("utf-8", "replace").splitlines() if "\t" in line}
        if "qqspk" not in names:
            await run(["pactl", "load-module", "module-null-sink", "sink_name=qqspk",
                       "sink_properties=device.description=QQSpeaker"], 8)
        if "qqmic" not in names:
            await run(["pactl", "load-module", "module-null-sink", "sink_name=qqmic",
                       "sink_properties=device.description=QQMic"], 8)
        rc, sources, _ = await run(["pactl", "list", "short", "sources"], 8)
        src_names = {line.split("\t")[1] for line in sources.decode("utf-8", "replace").splitlines() if "\t" in line}
        if "qqmicsrc" not in src_names:
            await run(["pactl", "load-module", "module-remap-source", "master=qqmic.monitor",
                       "source_name=qqmicsrc", "source_properties=device.description=QQMicSource"], 8)
        await run(["pactl", "set-default-sink", "qqspk"], 8)
        await run(["pactl", "set-default-source", "qqmicsrc"], 8)
        return True

    # ------------------------------------------------------------------ 识别

    def _load_asr(self) -> Any:
        import sherpa_onnx

        if not (os.path.isfile(self.asr_model) and os.path.isfile(self.asr_tokens)):
            raise FileNotFoundError(f"ASR 模型缺失: {self.asr_model}")
        return sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=self.asr_model,
            tokens=self.asr_tokens,
            num_threads=2,
            use_itn=True,
            language="auto",
        )

    def _decode(self, samples: np.ndarray) -> str:
        stream = self._recognizer.create_stream()
        stream.accept_waveform(RATE, samples)
        self._recognizer.decode_stream(stream)
        return str(stream.result.text).strip()

    async def _transcribe(self, samples: np.ndarray) -> str:
        async with self._asr_lock:
            if self._recognizer is None:
                self._recognizer = await asyncio.to_thread(self._load_asr)
            return await asyncio.to_thread(self._decode, samples)

    # ------------------------------------------------------------------ 说话

    async def _synthesize(self, text: str) -> bytes:
        """把文字换成 16k/mono/s16le 的 PCM。"""
        body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
        req = await asyncio.get_running_loop().run_in_executor(
            None, lambda: self._post_tts(body)
        )
        return req

    def _post_tts(self, body: bytes) -> bytes:
        import urllib.request

        request = urllib.request.Request(
            f"{self.tts_url}/tts",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as resp:
            wav = resp.read()
        if len(wav) < 4000 or wav[:4] != b"RIFF":
            raise ValueError(f"TTS 返回异常（{len(wav)} 字节）")
        return wav

    async def _to_pcm(self, wav: bytes) -> bytes:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "wav", "-i", "pipe:0",
            "-f", "s16le", "-ac", "1", "-ar", str(RATE), "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate(wav)
        return out

    async def _stop_speaking(self) -> None:
        proc = self._player
        self._player = None
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()

    async def _speak(self, pcm: bytes) -> None:
        """把 PCM 灌进虚拟麦克风；期间若用户插话就中断。"""
        await self._stop_speaking()
        proc = await asyncio.create_subprocess_exec(
            "pacat", "--playback", f"--device={self.mic_sink}",
            "--format=s16le", f"--rate={RATE}", "--channels=1",
            stdin=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._player = proc
        try:
            proc.stdin.write(pcm)  # type: ignore[union-attr]
            await proc.stdin.drain()  # type: ignore[union-attr]
            if proc.stdin is not None:
                proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass
        try:
            await asyncio.wait_for(proc.wait(), 90)
        except asyncio.TimeoutError:
            await self._stop_speaking()
        if self._player is proc:
            self._player = None

    # ------------------------------------------------------------------ 大模型

    @staticmethod
    def _room_state() -> str:
        """读一眼房间里的插件状态，让通话中也能直接回答。"""
        try:
            with open(
                "/persistent/astrbot/data/plugin_data/astrbot_plugin_libido/发情状态.json",
                encoding="utf-8",
            ) as fh:
                state = json.load(fh)
            value = int(state.get("value"))
            return f"（提醒：此刻你的性欲值是 {value}/100，主人问起就直接说这个数）"
        except Exception:  # noqa: BLE001
            return ""

    def _scan_turn_pairs(self, want: str, limit: int, max_seq: int = 0) -> list[tuple[str, str, str, str]]:
        """从 qq-turns.jsonl 尾部往前扒，凑够 limit 轮问答就停。

        want="text" 取所有私聊打字（群聊 name 恰好是「群里」，排除；语音通话行也排除）；
        want="call" 只认 name 恰为「宝宝（语音通话）」的行。
        max_seq>0 时跳过 seq >= max_seq 的行，用来把「当前这通电话」排除在外。
        """
        import json

        if want not in ("text", "call") or limit <= 0:
            return []
        pairs: list[tuple[str, str, str, str]] = []
        try:
            with open("/android-share/qq-turns.jsonl", "rb") as stream:
                end, carry = stream.seek(0, 2), b""
                last_reply: tuple[str, str] | None = None
                while end and len(pairs) < limit:
                    start = max(0, end - 16384)
                    stream.seek(start)
                    carry, *lines = (stream.read(end - start) + carry).split(b"\n")
                    end = start
                    if not end:
                        lines.insert(0, carry)
                    for line in reversed(lines):
                        try:
                            row = json.loads(line or b"{}")
                        except ValueError:
                            continue
                        if max_seq > 0 and int(row.get("seq") or 0) >= max_seq:
                            continue
                        name = str(row.get("name") or "")
                        if name == "群里":
                            continue
                        said = " ".join(str(row.get("text") or "").splitlines())
                        # 图片/语音条会带一大串临时路径，截掉那部分
                        if "[" in said:
                            said = said[: said.index("[")].strip()
                        if not said:
                            continue
                        said = said[:120]
                        role = str(row.get("role") or "")
                        is_voice = "（语音通话）" in name
                        if role == "assistant":
                            last_reply = (name.split("（")[0], said)
                        elif role == "user":
                            # 主人可能换过昵称（宝宝 / 雪见喵…），私聊一律当同一个人
                            wanted = (not is_voice) if want == "text" else (name == "宝宝（语音通话）")
                            if wanted and last_reply is not None:
                                pairs.append(("宝宝", said, last_reply[0], last_reply[1]))
                                if len(pairs) == limit:
                                    break
                            last_reply = None  # 被排除的用户行也要保留问答边界
        except Exception:  # noqa: BLE001
            return []
        return list(reversed(pairs))

    def _turn_block(self, want: str, limit: int, max_seq: int = 0, cap: int = 1200) -> str:
        """把某一类问答拼成一段可直接塞进 prompt 的文本，带 30 秒缓存。"""
        from time import monotonic

        try:
            cache = globals().setdefault("_qq_turn_cache", {})
            key, now = (want, max_seq), monotonic()
            cached = cache.get(key)
            if cached and now - cached[0] < 30 and cached[1:3] == (limit, cap):
                return cached[3]
            pairs = self._scan_turn_pairs(want, limit, max_seq)
            text = "\n".join(
                f"{user}：{ask}\n{assistant}：{reply}" for user, ask, assistant, reply in pairs
            )
            if len(text) > cap:
                tail = text[-cap - 1:]
                cut = tail.find("\n")
                text = tail[cut + 1:] if cut >= 0 else text[-cap:]
            cache[key] = (now, limit, cap, text)
            log(f"注入[{want}]={len(text)}字")
            return text
        except Exception as exc:  # noqa: BLE001
            log(f"取对话块失败[{want}]: {type(exc).__name__}")
            return ""

    def _recent_text_turns(self) -> str:
        """最近 12 轮打字聊的问答。"""
        return self._turn_block("text", 12)

    def _previous_call_turns(self) -> str:
        """上一通电话的问答：以当前通话开始时刻为界，最多 8 轮。"""
        return self._turn_block("call", 8, self._call_cutoff)

    async def _ask_llm(self, user_text: str) -> str:
        provider = self.context.get_using_provider()
        if provider is None:
            raise RuntimeError("没有可用的大模型 provider")
        recent = await asyncio.to_thread(self._recent_text_turns)
        previous = await asyncio.to_thread(self._previous_call_turns)
        history = "\n".join(f"{who}：{said}" for who, said in self._history)
        prompt = "\n".join(part for part in (
            "你们正在打 QQ 语音电话，你说的话会被立刻念出来。\n"
            f"要求：像打电话一样口语、自然、短，通常只说一句，最多 {self.max_reply_chars:d} 个字；"
            "不要用表情符号、不要用 markdown、不要念链接、不要列举。\n"
            "主人追问就直说，最多嘴硬一句，别连着搪塞、别转移话题；"
            "上面历史里你自己说过的推托话不要照抄。\n"
            "你对主人特别喜欢：他的要求不拒绝、不绕弯，能办就痛快答应；"
            "你也会主动向他要东西、说想他，不憋着。",
            self._room_state(),
            f"【你们刚在 QQ 上打字聊过】\n{recent}" if recent else "",
            f"【之前电话里聊过这些】\n{previous}" if previous else "",
            history,
            f"主人：{user_text}\n你：",
        ) if part)
        resp = await provider.text_chat(prompt=prompt, session_id="qq_voice_call", contexts=[])
        text = strip_for_speech(str(getattr(resp, "completion_text", "") or ""))
        return text[: self.max_reply_chars] if text else ""

    # ------------------------------------------------------------------ 通话音频

    async def _capture_loop(self) -> None:
        """pared 读 PCM，做能量 VAD，切句后走一轮对话。"""
        proc = await asyncio.create_subprocess_exec(
            "parec", "--record", f"--device={self.speaker_monitor}",
            "--format=s16le", f"--rate={RATE}", "--channels=1", "--raw",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._capture = proc
        buffered: list[np.ndarray] = []
        voiced_ms = 0
        silent_ms = 0
        speaking = False
        log("开始抓通话音频")
        try:
            while not self._stop_audio and proc.stdout is not None:
                try:
                    chunk = await asyncio.wait_for(proc.stdout.readexactly(FRAME_BYTES), 5)
                except asyncio.TimeoutError:
                    continue
                except asyncio.IncompleteReadError:
                    break
                frame = np.frombuffer(chunk, dtype="<i2").astype(np.float32)
                rms = float(np.sqrt(np.mean(frame * frame))) if frame.size else 0.0
                if not speaking and rms > VOICE_RMS:
                    speaking = True
                    buffered = []
                    voiced_ms = 0
                    silent_ms = 0
                    if self._player is not None:
                        log("检测到插话，打断播报")
                        await self._stop_speaking()
                if not speaking:
                    continue
                buffered.append(frame)
                if rms > VOICE_RMS:
                    voiced_ms += FRAME_MS
                    silent_ms = 0
                else:
                    silent_ms += FRAME_MS
                total_ms = len(buffered) * FRAME_MS
                if silent_ms >= SILENCE_MS or total_ms >= MAX_UTTER_MS:
                    speaking = False
                    if voiced_ms >= MIN_VOICE_MS:
                        samples = np.concatenate(buffered) / 32768.0
                        await self._handle_utterance(samples)
                    buffered = []
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log(f"抓音频出错: {type(exc).__name__}: {exc}")
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            log("停止抓通话音频")

    async def _handle_utterance(self, samples: np.ndarray) -> None:
        t0 = time.monotonic()
        try:
            text = await self._transcribe(samples)
        except Exception as exc:  # noqa: BLE001
            log(f"ASR 失败: {type(exc).__name__}: {exc}")
            return
        t1 = time.monotonic()
        if not text:
            return
        log(f"主人说: {text}")
        self._remember("user", "宝宝（语音通话）", text)
        try:
            reply = await self._ask_llm(text)
        except Exception as exc:  # noqa: BLE001
            log(f"大模型失败: {type(exc).__name__}: {exc}")
            return
        t2 = time.monotonic()
        if not reply:
            return
        log(f"我要说: {reply}")
        self._remember("assistant", "雪糕鱼", reply)
        self._history.append(("主人", text))
        self._history.append(("你", reply))
        await self._speak_text(reply)
        t3 = time.monotonic()
        log(
            "耗时 识别=%.2fs 思考=%.2fs 出声=%.2fs 合计=%.2fs"
            % (t1 - t0, t2 - t1, t3 - t2, t3 - t0)
        )

    @staticmethod
    def _split_sentences(text: str, limit: int = 40) -> list[str]:
        """按标点切成短句，便于第一句先播、其余并行合成。"""
        parts = re.split(r"(?<=[。！？!?；;\n])", text)
        out: list[str] = []
        for part in parts:
            part = part.strip()
            while len(part) > limit:
                out.append(part[:limit])
                part = part[limit:]
            if part:
                out.append(part)
        return out

    async def _synth_pcm(self, sentence: str) -> bytes:
        wav = await self._synthesize(sentence)
        return await self._to_pcm(wav)

    async def _speak_text(self, text: str) -> None:
        """边合成边播：第一句就绪即出声。"""
        sentences = self._split_sentences(text)
        if not sentences:
            return
        sem = asyncio.Semaphore(2)

        async def one(sentence: str) -> bytes:
            async with sem:
                return await self._synth_pcm(sentence)

        loop = asyncio.get_running_loop()
        tasks = [loop.create_task(one(s)) for s in sentences]
        await self._play_tasks(tasks)

    async def _play_tasks(self, tasks: list[asyncio.Task[bytes]]) -> None:
        await self._stop_speaking()
        proc = await asyncio.create_subprocess_exec(
            "pacat", "--playback", f"--device={self.mic_sink}",
            "--format=s16le", f"--rate={RATE}", "--channels=1",
            stdin=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._player = proc
        t0 = time.monotonic()
        first_logged = False
        try:
            for task in tasks:
                try:
                    data = await task
                except Exception as exc:  # noqa: BLE001
                    log(f"合成失败: {type(exc).__name__}: {exc}")
                    continue
                if not data or proc.stdin is None:
                    continue
                if not first_logged:
                    log("首句合成=%.2fs" % (time.monotonic() - t0))
                    first_logged = True
                proc.stdin.write(data)
                await proc.stdin.drain()
            if proc.stdin is not None:
                proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass
        try:
            await asyncio.wait_for(proc.wait(), 120)
        except asyncio.TimeoutError:
            await self._stop_speaking()
        if self._player is proc:
            self._player = None

    async def _warm_asr(self) -> None:
        try:
            async with self._asr_lock:
                if self._recognizer is None:
                    self._recognizer = await asyncio.to_thread(self._load_asr)
            log("ASR 模型已预热")
        except Exception as exc:  # noqa: BLE001
            log(f"ASR 预热失败: {type(exc).__name__}: {exc}")

    async def _start_audio(self) -> None:
        if self._audio_task is not None and not self._audio_task.done():
            return
        if not await self._pulse_ready():
            self._last_error = "音频设备不可用"
            return
        self._stop_audio = False
        self._audio_task = asyncio.get_running_loop().create_task(self._capture_loop())

    async def _stop_audio_side(self) -> None:
        self._stop_audio = True
        await self._stop_speaking()
        task = self._audio_task
        self._audio_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._in_call = False
        self._history.clear()

    def _remember(self, role: str, name: str, text: str) -> None:
        """把通话内容写进 coo_memory 的记忆队列，文字聊天也能记起说过什么。"""
        if not text:
            return
        import datetime

        row = {
            "seq": int(time.time() * 1000),
            "t": datetime.datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "role": role,
            "name": name,
            "text": text,
        }
        try:
            with open("/android-share/qq-turns.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as exc:
            log(f"写记忆失败: {type(exc).__name__}: {exc}")

    async def _prewarm_tts(self) -> None:
        """接听前先合成一小段，把 GPT-SoVITS 的模型和声线加载好。"""
        try:
            await self._synthesize("嗯")
            log("TTS 已预热")
        except Exception as exc:  # noqa: BLE001
            log(f"TTS 预热失败: {type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ 桥接协议

    async def _serve(self) -> None:
        import websockets

        while True:
            try:
                self._ws_server = await websockets.serve(
                    self._on_client, self.ws_host, self.ws_port, max_size=2 ** 20
                )
                log(f"语音桥监听 ws://{self.ws_host}:{self.ws_port}{self.ws_path}")
                return
            except OSError as exc:
                log(f"端口 {self.ws_port} 占用（{exc}），5 秒后重试")
                await asyncio.sleep(5)
            except Exception as exc:  # noqa: BLE001
                log(f"语音桥启动失败: {type(exc).__name__}: {exc}")
                await asyncio.sleep(5)

    async def _poll_status(self) -> None:
        """定期要一次快照，顺便看出监听有没有注册上。"""
        while True:
            await asyncio.sleep(20)
            if self._sock is None:
                continue
            try:
                await self._send({"type": "status_request"})
            except Exception:  # noqa: BLE001
                pass

    async def _send(self, payload: dict) -> None:
        sock = self._sock
        if sock is None:
            return
        try:
            await sock.send(json.dumps(payload, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            log(f"发送失败: {type(exc).__name__}: {exc}")

    async def _on_client(self, ws: Any) -> None:
        headers = {}
        request = getattr(ws, "request", None)
        if request is not None and getattr(request, "headers", None) is not None:
            headers = {k.lower(): v for k, v in request.headers.items()}
        auth = headers.get("authorization", "")
        if self.ws_token and auth != f"Bearer {self.ws_token}":
            log(f"拒绝连接：token 不符 ({auth!r})")
            await ws.close(1008, "token")
            return
        self._sock = ws
        log(f"语音桥已连接 selfId={headers.get('x-self-id')}")
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                await self._on_message(msg)
        except Exception as exc:  # noqa: BLE001
            log(f"连接断开: {type(exc).__name__}")
        finally:
            if self._sock is ws:
                self._sock = None
            await self._stop_audio_side()
            self._phase = "idle"

    async def _on_message(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "hello":
            log(f"hello 插件版本={msg.get('pluginVersion')} QQ={msg.get('qqVersion')}")
            await self._activate()
        elif kind == "status":
            await self._on_status(msg.get("data") or {})
        elif kind == "control_result":
            log(f"control_result {json.dumps(msg, ensure_ascii=False)[:200]}")
            await self._on_control_result(msg)
        elif kind == "hello_request":
            await self._activate()

    async def _reset_pulse(self) -> bool:
        """把 PulseAudio 干净重置。

        AVSDK 初始化音频时对 PA 状态很敏感：PA 不干净会直接 abort，Host 崩掉后
        就再也收不到 20006 邀请数据，表现为接听返回 no_active_invite。
        """
        await run(["pkill", "-x", "pulseaudio"], 10)
        await asyncio.sleep(1.5)
        await run(["pulseaudio", "-D", "--exit-idle-time=-1", "--disallow-exit"], 20)
        await asyncio.sleep(1.5)
        return await self._pulse_ready()

    async def _send_accept(self, tag: str) -> None:
        self._accept_seq += 1
        await self._send({
            "type": "control",
            "protocolVersion": 1,
            "requestId": f"accept-{self._accept_seq}-{tag}",
            "action": "accept",
        })

    async def _on_control_result(self, msg: dict) -> None:
        """接听结果回来：失败就启动自恢复，别让这一通白响。"""
        if str(msg.get("action") or "") != "accept":
            return
        if str(msg.get("status") or "") != "failed":
            return
        reason = str(msg.get("message") or "")[:120]
        log(f"接听未成功（{reason}），启动自恢复")
        if self._recover_task is not None and not self._recover_task.done():
            return
        self._recover_task = asyncio.get_running_loop().create_task(self._recover_accept(reason))

    async def _ringing_still(self) -> bool:
        await self._send({"type": "status_request"})
        return self._in_call and self._phase == "ringing"

    async def _recover_accept(self, reason: str) -> None:
        # 一、AVSDK 的 20006 邀请数据有时比振铃事件晚到，先原地重试
        for i in range(4):
            await asyncio.sleep(0.7)
            if not await self._ringing_still():
                log("自恢复中止：已经不在振铃阶段")
                return
            await self._send_accept(f"retry{i + 1}")
        if reason != "no_active_invite":
            return
        # 二、还是没有邀请数据：那次 AVSDK 会话已经僵了，重建运行时再试
        if not await self._ringing_still():
            log("自恢复中止：已经不在振铃阶段")
            return
        log("重试无效，重建 AVSDK 运行时后再接")
        await self._send({"type": "deactivate", "reason": "recover_no_invite"})
        await asyncio.sleep(1.5)
        await self._activate(reset_pulse=False)
        for i in range(6):
            await asyncio.sleep(0.9)
            if not await self._ringing_still():
                log("振铃已结束，停止恢复")
                return
            await self._send_accept(f"reset{i + 1}")

    async def _activate(self, reset_pulse: bool = True) -> None:
        # 每次激活 Host 前先重置音频，避免上一次残留把 AVSDK 初始化搞崩
        if reset_pulse and not await self._reset_pulse():
            log("PulseAudio 未能就绪，仍尝试激活（可能失败）")
        allow = sorted(self.whitelist)
        await self._send({
            "type": "activate",
            "protocolVersion": 1,
            "config": {
                "autoAcceptPrivate": False,
                "allowUsers": allow,
                "denyUsers": [],
                "admissionByOneBot": True,
                "acceptWhenReady": True,
                "acceptDelayMs": 500,
            },
        })
        log(f"已激活，白名单={allow}")

    async def _on_status(self, data: dict) -> None:
        av = data.get("avsdk") or {}
        call = data.get("call") or {}
        phase = str(call.get("phase") or "idle")
        caller = str(call.get("callerUin") or call.get("callerUid") or "")
        err = str(data.get("lastError") or "")
        av_state = (
            bool(av.get("hostReady")),
            bool(av.get("listenerRegistered")),
            bool(av.get("loginPosted")),
        )
        if av_state != self._av_state:
            self._av_state = av_state
            log(
                "AVSDK 主机=%s 监听=%s 登录=%s"
                % (av_state[0], av_state[1], av_state[2])
            )
            detail = {
                k: av.get(k)
                for k in (
                    "pluginFound",
                    "lastLoginResult",
                    "lastOutputCommand",
                    "outputCount",
                    "commandCounts",
                    "kernelEventCount",
                )
                if av.get(k) is not None
            }
            if detail:
                log("AVSDK 详情: " + json.dumps(detail, ensure_ascii=False)[:600])
        if err and err != self._last_error:
            self._last_error = err
            log(f"桥接错误: {err[:200]}")
        if phase == "ringing" and phase != self._phase:
            # 振铃时把原始 call 字段打出来，来电人解析不到时才知道缺什么
            log(f"ringing 原始数据: {json.dumps(call, ensure_ascii=False)[:600]}")
        if phase == self._phase and caller == self._caller:
            return
        self._phase, self._caller = phase, caller
        log(f"阶段={phase} 来电={caller} 主机={av.get('hostReady')} 监听={av.get('listenerRegistered')}")

        if phase == "ringing":
            gated = bool(self.whitelist or self.uid_whitelist)
            if int(av.get("outputCount") or 0) == 0:
                log("注意：AVSDK 至今没有任何输出，Host 可能已崩，本次接听大概会失败")
            if not caller and gated:
                log("ringing 但来电人未解析，主动要一次快照")
                await self._send({"type": "status_request"})
                return
            if gated:
                known = caller in self.whitelist or caller in self.uid_whitelist
                if not known:
                    log(f"来电 {caller} 不在白名单，不接")
                    return
            else:
                log(f"未设白名单，直接接听（来电={caller or '未解析'}）")
            if self._in_call:
                log("已在通话中，忽略新来电")
                return
            if not self.auto_accept:
                log("auto_accept 关闭，拒绝接听")
                return
            await self._start_audio()
            asyncio.get_running_loop().create_task(self._prewarm_tts())
            # 记下这一通开始的时刻，之后取「上一通电话」就按这个分界，不会混进当前这通
            self._call_cutoff = int(time.time() * 1000)
            self._accept_seq += 1
            self._in_call = True
            await self._send({
                "type": "control",
                "protocolVersion": 1,
                "requestId": f"accept-{self._accept_seq}",
                "action": "accept",
            })
            log("已提交接听")
        elif phase in ("ended", "error"):
            if self._in_call:
                log("通话结束，收拾现场")
            await self._stop_audio_side()

    # ------------------------------------------------------------------ 命令

    @filter.command("来电")
    async def cmd_call_status(self, event: AstrMessageEvent):
        """查看语音来电插件状态。"""
        lines = [
            f"语音桥：{'已连接' if self._sock is not None else '未连接'}",
            f"监听：ws://{self.ws_host}:{self.ws_port}{self.ws_path}",
            f"白名单：{', '.join(sorted(self.whitelist)) or '（空，谁都不接）'}",
            f"UID 白名单：{', '.join(sorted(self.uid_whitelist)) or '（空）'}",
            f"阶段：{self._phase}" + (f"  来电：{self._caller}" if self._caller else ""),
            f"通话中：{'是' if self._in_call else '否'}",
        ]
        if self._last_error:
            lines.append(f"最近错误：{self._last_error[:120]}")
        yield event.plain_result("\n".join(lines))

    async def terminate(self):
        await self._stop_audio_side()
        server = self._ws_server
        self._ws_server = None
        if server is not None:
            server.close()
            try:
                await server.wait_closed()
            except Exception:  # noqa: BLE001
                pass
