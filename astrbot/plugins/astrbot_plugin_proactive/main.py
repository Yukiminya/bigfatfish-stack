import asyncio
import fcntl
import importlib.util
import json
import os
import random
import re
import sqlite3
import sys
import threading
import time
import unicodedata
from collections import deque
from collections.abc import AsyncIterator, Iterator
from contextlib import closing, suppress
from datetime import datetime, time as wall_time, timedelta, timezone
from pathlib import Path
from typing import BinaryIO

import astrbot.core.message.components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star, register

try:
    from astrbot.core.message.message_event_result import MessageChain
except ImportError:
    from astrbot.api.event import MessageChain


STATE_PATH = Path("/persistent/astrbot/data/plugin_data/astrbot_plugin_proactive_state.json")
PERSONA_URI = "file:/persistent/astrbot/data/data_v4.db?mode=ro"
TURNS_PATH = Path("/android-share/qq-turns.jsonl")
LOG_PATH = Path("/android-share/proactive-log.jsonl")
VOICE_ROOT = Path("/android-share/proactive-voice")
PLUGINS_ROOT = Path("/persistent/astrbot/data/plugins")
LIBIDO_CORE = PLUGINS_ROOT / "astrbot_plugin_libido" / "libido_core.py"
LIBIDO_STATE = Path("/persistent/astrbot/data/plugin_data/astrbot_plugin_libido/发情状态.json")
MODULE_LOCK = threading.Lock()
TARGET_UMO = "napcat:FriendMessage:641961016"
CHECK_SEC = 60
# 同一跳过原因在这个间隔内只记一次，避免静默时段每分钟刷一行日志
SKIP_LOG_GAP = 1800
BOT_NAME = "雪糕鱼"
OTHER_CHATS = {"群里", "宝宝（语音通话）"}
STYLE = "你是雪糕鱼，正在 QQ 私聊里陪主人聊天。说话亲近、自然、口语化，延续平时的语气。"
DEFAULT_TOPICS = (
    "今天在忙什么", "游戏和视频", "你的身体累不累", "心情", "天气",
    "想主人", "你自己的一天", "家里和吃的", "以前聊过的事", "临时冒出来的怪念头",
)
TOPIC_HINTS = {
    "早上": ("今天", "天气", "心情"),
    "下午": ("游戏", "视频", "在忙", "身体"),
    "晚上": ("你自己的一天", "家里", "吃的", "身体", "以前"),
    "深夜": ("想主人", "心情", "以前", "怪念头"),
}
SENTENCE_BREAK = re.compile(r"[。！？!?…\r\n]+|(?<!\d)\.+|\.+(?!\d)")
FORMAT_MARKS = re.compile(
    r"[`*_~<>()[\]{}【】（）]|"
    r"(?m:^\s*(?:[-+•●▪]\s*|#{1,6}\s*|\d+[、)]|\d+\.(?!\d)|[一二三四五六七八九十]+、))"
)
LINK = re.compile(
    r"[a-z][a-z0-9+.-]*://|www\.|"
    r"(?<![a-z0-9.-])(?:[a-z0-9-]+\.)+(?:xn--[a-z0-9-]+|[a-z]{2,})(?![a-z0-9-])",
    re.IGNORECASE,
)


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8", errors="backslashreplace")


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _load_module(path: Path, suffix: str):
    name = f"_astrbot_proactive_{suffix}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块 {path}")
    module = importlib.util.module_from_spec(spec)
    # 注册后再执行，兼容使用数据类等依赖模块注册的代码。
    with MODULE_LOCK:
        previous = sys.modules.get(name)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
            raise
    return module


def _read_libido_value() -> int | None:
    try:
        saved = json.loads(LIBIDO_STATE.read_text(encoding="utf-8"))
        value = saved.get("value") if isinstance(saved, dict) else None
        if type(value) in (int, float) and 0 <= value <= 100:
            return int(value)
        logger.info("主动消息的性欲状态文件没有有效数值。")
    except (OSError, ValueError, RecursionError) as exc:
        logger.info(f"主动消息读取性欲状态失败。{exc}")
    return None


def _read_libido() -> tuple[int | None, str]:
    value = _read_libido_value()
    if value is None:
        return None, ""
    try:
        module = _load_module(LIBIDO_CORE, "libido_core")
        mood = module.LibidoState(LIBIDO_STATE).mood_line()
        return value, mood if isinstance(mood, str) else ""
    except Exception as exc:
        logger.info(f"主动消息读取性欲状态描述失败，跳过状态段。{exc}")
        return value, ""


def _load_remote():
    for directory in ("astrbot_plugin_feiyu_voice", "feiyu_voice", "astrbot_plugin_feiyu", "feiyu"):
        path = PLUGINS_ROOT / directory / "remote_tts.py"
        if path.is_file():
            return _load_module(path, "remote_tts")
    raise FileNotFoundError("找不到飞鱼语音插件的 remote_tts.py")


def _make_voice_path() -> Path:
    VOICE_ROOT.mkdir(parents=True, exist_ok=True)
    millis = time.time_ns() // 1_000_000
    path = VOICE_ROOT / f"{millis}.wav"
    while path.exists():
        millis += 1
        path = VOICE_ROOT / f"{millis}.wav"
    return path


def _reverse_lines(file: BinaryIO) -> Iterator[bytes]:
    # 从末尾倒着找完整行，避免每轮扫描整个长期流水。
    offset = file.seek(0, os.SEEK_END)
    tail = b""
    while offset:
        size = min(8192, offset)
        offset -= size
        file.seek(offset)
        tail, *lines = (file.read(size) + tail).split(b"\n")
        yield from reversed(lines)
    if tail:
        yield tail


def _read_turns() -> tuple[list[dict], int | None]:
    turns = []
    last_user_ms = None
    try:
        with TURNS_PATH.open("rb") as file:
            for line in _reverse_lines(file):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line.decode("utf-8"))
                except (UnicodeError, ValueError, RecursionError):
                    continue
                if not isinstance(row, dict):
                    continue
                role, name = row.get("role"), row.get("name")
                seq, stamp, text = row.get("seq"), row.get("t"), row.get("text")
                if (
                    role not in ("user", "assistant")
                    or not isinstance(name, str)
                    or name in OTHER_CHATS
                    or type(seq) is not int
                    or not isinstance(stamp, str)
                    or not isinstance(text, str)
                ):
                    continue
                if role == "user" and last_user_ms is None:
                    last_user_ms = seq
                if len(turns) < 8:
                    turns.append({"t": stamp, "role": role, "name": name, "text": text})
                if len(turns) == 8 and last_user_ms is not None:
                    break
    except OSError as exc:
        logger.warning(f"主动消息读取 QQ 流水失败，使用已读记录。{exc}")
    return list(reversed(turns)), last_user_ms


def _read_persona() -> str:
    try:
        with closing(sqlite3.connect(PERSONA_URI, uri=True, timeout=1.0)) as conn:
            row = conn.execute(
                "SELECT system_prompt FROM personas WHERE persona_id = ? LIMIT 1",
                ("default",),
            ).fetchone()
        if row and isinstance(row[0], str) and row[0].strip():
            return row[0]
    except (OSError, sqlite3.Error) as exc:
        logger.info(f"主动消息读取人设失败，使用内置人设。{exc}")
    return ""


def _append_turn(sent_at: datetime, text: str) -> None:
    row = {
        "seq": int(sent_at.timestamp() * 1000),
        "t": sent_at.isoformat(),
        "role": "assistant",
        "name": BOT_NAME,
        "text": text,
    }
    try:
        with TURNS_PATH.open("a+b") as file:
            # 保持文件锁直至关闭，确保缓冲区写入也在锁内。
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            prefix = b""
            if file.seek(0, os.SEEK_END):
                file.seek(-1, os.SEEK_END)
                if file.read(1) != b"\n":
                    prefix = b"\n"
            file.write(prefix + _json_bytes(row) + b"\n")
    except OSError as exc:
        logger.warning(f"主动消息追加 QQ 流水失败。{exc}")


def _record(
    action: str,
    reason: str,
    text: str = "",
    *,
    tz: timezone,
    libido: int | None = None,
    voice: str | None = None,
) -> None:
    log = logger.warning if action == "error" else logger.info
    log(f"主动消息：{reason}")
    row = {
        "ts": datetime.now(tz).isoformat(),
        "action": action,
        "reason": reason,
        "text": text,
        "libido": libido,
        "voice": voice,
    }
    try:
        try:
            with LOG_PATH.open("rb") as file:
                lines = deque((line for line in file if line.strip()), maxlen=199)
        except FileNotFoundError:
            lines = deque(maxlen=199)
        data = b"".join(line.rstrip(b"\r\n") + b"\n" for line in lines)
        _atomic_write(LOG_PATH, data + _json_bytes(row) + b"\n")
    except OSError as exc:
        logger.warning(f"主动消息写入日志文件失败。{exc}")


def _quiet_time(value: str, default: str) -> wall_time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except (TypeError, ValueError):
        logger.warning(f"主动消息的静默时间格式无效，使用默认值 {default}。")
        return datetime.strptime(default, "%H:%M").time()


def _period(now: datetime) -> str:
    return ("深夜", "早上", "下午", "晚上")[now.hour // 6]


def _sentence_keys(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    keys = []
    for part in SENTENCE_BREAK.split(normalized):
        key = "".join(char for char in part if char.isalnum())
        if key:
            keys.append(key)
    return keys


@register("astrbot_plugin_proactive", "xuegaoyu", "不定时主动发消息", "v1.1.0")
class Proactive(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.context = context
        self.config = config if config is not None else {}
        self.min_gap = max(0, self.config.get("min_gap_min", 40)) * 60
        self.max_gap = max(self.min_gap, self.config.get("max_gap_min", 150) * 60)
        self.quiet_start = _quiet_time(self.config.get("quiet_start", "02:00"), "02:00")
        self.quiet_end = _quiet_time(self.config.get("quiet_end", "08:30"), "08:30")
        self.daily_cap = max(0, self.config.get("daily_cap", 12))
        self.idle_gap = max(0, self.config.get("idle_gap_min", 25)) * 60
        self.max_chars = max(1, self.config.get("max_chars", 60))
        self.use_persona = self.config.get("use_persona", True)
        self.target_umo = self.config.get("target_umo", TARGET_UMO)
        self.utc_offset_hours = self.config.get("utc_offset_hours", 8)
        try:
            self._tz = timezone(timedelta(hours=self.utc_offset_hours))
        except (TypeError, ValueError, OverflowError):
            logger.warning("主动消息的时区偏移无效，使用北京时间。")
            self.utc_offset_hours = 8
            self._tz = timezone(timedelta(hours=8))

        configured_topics = self.config.get("topics", [])
        if not isinstance(configured_topics, list):
            configured_topics = []
        self.topics = list(dict.fromkeys(
            item.strip() for item in configured_topics if isinstance(item, str) and item.strip()
        )) or list(DEFAULT_TOPICS)
        self.use_libido = self.config.get("use_libido", True)
        self.with_voice = self.config.get("with_voice", True)
        self.voice_dir = self.config.get("voice_dir", "/android-share/tts")
        self.voice_name = (self.config.get("voice_name") or "").strip()
        self.voice_speed = self.config.get("voice_speed", 1.0)
        self.voice_max_chars = max(0, self.config.get("voice_max_chars", 60))
        self.remote_urls = [
            url.strip() for url in (self.config.get("remote_tts_urls") or "").split(",") if url.strip()
        ]
        self._local_voice = None
        self._remote = None

        now = datetime.now(self._tz)
        self.state = {
            "enabled": True,
            "last_sent_ts": 0.0,
            "sent_today": 0,
            "day": now.date().isoformat(),
            "recent_texts": [],
            "recent_topics": [],
        }
        self._disk_bytes = None
        self._dirty = False
        self._next_due = 0.0
        self._send_lock = asyncio.Lock()
        self._skip_seen: dict[str, float] = {}
        self._refresh_state(create=True)
        self._plan_next(resume=True)
        self._task = asyncio.create_task(self._run(), name="astrbot_plugin_proactive")
        due = datetime.fromtimestamp(self._next_due, tz=self._tz)
        logger.info(f"主动消息定时任务已启动，下次定时检查约 {due:%Y-%m-%d %H:%M}。")

    async def terminate(self) -> None:
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task

    def _save_state(self) -> bool:
        self._dirty = True
        data = _json_bytes(self.state) + b"\n"
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(STATE_PATH, data)
        except OSError as exc:
            logger.warning(f"主动消息保存状态失败，稍后重试。{exc}")
            return False
        self._disk_bytes = data
        self._dirty = False
        return True

    def _refresh_state(self, create: bool = False) -> None:
        try:
            raw = STATE_PATH.read_bytes()
            if raw == self._disk_bytes:
                return
            saved = json.loads(raw)
            if not isinstance(saved, dict):
                raise ValueError("状态文件应为 JSON 对象")
        except FileNotFoundError:
            if create:
                self._save_state()
            return
        except (OSError, ValueError, RecursionError) as exc:
            logger.warning(f"主动消息读取状态失败，沿用内存状态。{exc}")
            return

        self._disk_bytes = raw
        if type(saved.get("enabled")) is bool:
            self.state["enabled"] = saved["enabled"]
        # 保存失败时保留本轮已更新的统计，仍接受外部开关修改。
        if self._dirty:
            return
        last = saved.get("last_sent_ts")
        old_last = self.state["last_sent_ts"]
        if type(last) in (int, float) and 0 <= last <= time.time():
            self.state["last_sent_ts"] = float(last)
        count = saved.get("sent_today")
        if type(count) is int and count >= 0:
            self.state["sent_today"] = count
        if isinstance(saved.get("day"), str):
            self.state["day"] = saved["day"]
        for key, limit in (("recent_texts", 20), ("recent_topics", 10)):
            recent = saved.get(key)
            if isinstance(recent, list):
                self.state[key] = [item for item in recent if isinstance(item, str)][-limit:]
        if "recent_topics" not in saved:
            self._dirty = True
        if self._next_due and old_last != self.state["last_sent_ts"]:
            self._plan_next(resume=True)

    def _roll_day(self, now: datetime) -> None:
        day = now.date().isoformat()
        if self.state["day"] != day:
            self.state["day"] = day
            self.state["sent_today"] = 0
            self._dirty = True

    def _plan_next(self, resume: bool = False) -> None:
        now = time.time()
        base = self.state["last_sent_ts"] or now
        self._next_due = base + random.uniform(self.min_gap, self.max_gap)
        if resume and self._next_due <= now:
            self._next_due = now + CHECK_SEC

    def _quiet_until(self, now: datetime) -> datetime | None:
        start, end = self.quiet_start, self.quiet_end
        wall = now.time()
        end_day = now.date()
        if start <= end:
            if not start <= wall < end:
                return None
        elif wall >= start:
            end_day += timedelta(days=1)
        elif wall >= end:
            return None
        return datetime.combine(end_day, end, tzinfo=self._tz)

    def _gate(self, manual: bool, last_user_ms: int | None = None) -> str:
        now = datetime.now(self._tz)
        self._roll_day(now)
        if not self.config.get("enabled", True):
            return "配置总开关已关闭"
        if not self.state["enabled"]:
            return "运行开关已关闭"
        if self._quiet_until(now) is not None:
            return "当前处于静默时段"
        if manual:
            return ""
        if self.state["sent_today"] >= self.daily_cap:
            return "今日已达发送上限"
        if last_user_ms is not None and int(now.timestamp() * 1000) - last_user_ms < self.idle_gap * 1000:
            return "主人最近在私聊说过话"
        if now.timestamp() - self.state["last_sent_ts"] < self.min_gap:
            return "距离上次发送还未到最短间隔"
        return ""

    def _choose_topic(self, period: str) -> str:
        blocked = self.state["recent_topics"][-3:]
        # 自定义方向少于四个时，从最旧的方向起逐步解除排除。
        for start in range(len(blocked) + 1):
            choices = [topic for topic in self.topics if topic not in blocked[start:]]
            if choices:
                break
        weights = [3 if any(hint in topic for hint in TOPIC_HINTS[period]) else 1 for topic in choices]
        return random.choices(choices, weights=weights, k=1)[0]

    def _synth_local(self, text: str, wav: Path) -> None:
        if self._local_voice is None:
            path = Path(__file__).resolve().with_name("voice_local.py")
            module = _load_module(path, "voice_local")
            self._local_voice = module.LocalVoice(self.voice_dir, self.voice_name, self.voice_speed)
        self._local_voice.synth(text, wav)

    async def _try_remote(self, text: str, wav: Path) -> bool:
        if not self.remote_urls:
            return False
        try:
            if self._remote is None:
                self._remote = await asyncio.to_thread(_load_remote)
            routes = [{"name": f"r{i}", "url": url} for i, url in enumerate(self.remote_urls)]
            data = await asyncio.wait_for(self._remote.synthesize_chain(text, routes), timeout=10)
            if data and self._remote.wav_ok(data):
                await asyncio.to_thread(_atomic_write, wav, data)
                return True
            logger.info("主动消息远程语音没有返回有效 WAV，改用本地合成。")
        except Exception as exc:
            logger.info(f"主动消息远程语音失败，改用本地合成。{exc}")
        return False

    async def _prepare_voice(self, text: str) -> tuple[Comp.Record | None, str | None]:
        if not self.with_voice or len(text) > self.voice_max_chars:
            return None, None
        try:
            wav = await asyncio.to_thread(_make_voice_path)
            voice = "remote"
            if not await self._try_remote(text, wav):
                await asyncio.to_thread(self._synth_local, text, wav)
                voice = "local"
            return Comp.Record.fromFileSystem(str(wav)), voice
        except Exception as exc:
            reason = str(exc) or type(exc).__name__
            logger.warning(f"主动消息准备语音失败，改发文字。{reason}")
            return None, f"fail:{reason}"

    async def _send(self, text: str, record: Comp.Record | None) -> bool:
        components = [Comp.Plain(text)]
        if record is not None:
            components.append(record)
        return await self.context.send_message(self.target_umo, MessageChain(components))

    async def _llm(self, prompt: str) -> str:
        provider = self.context.get_using_provider()
        if provider is None:
            raise RuntimeError("没有可用的大模型 provider")
        resp = await provider.text_chat(prompt=prompt, session_id="proactive", contexts=[])
        return str(getattr(resp, "completion_text", "") or "")

    async def _generate(self, turns: list[dict], now: datetime, topic: str, mood: str) -> tuple[str, str]:
        persona = await asyncio.to_thread(_read_persona) if self.use_persona else ""
        period = _period(now)
        history = _json_bytes(turns).decode("utf-8")
        parts = [STYLE]
        if persona:
            parts.append(f"当前人设全文\n{persona}")
        if mood.strip():
            parts.append(
                f"【你现在的状态】\n{mood}\n"
                "按这个状态说话。高值时黏人一点，可以表达想亲近。低值时平静关心。"
                "别报数值，别露骨。"
            )
        parts.extend([
            f"最近的私聊流水，仅供理解语气和话题\n{history}",
            f"现在是本地时间 {now:%Y-%m-%d %H:%M}，{period}。",
            f"【这次聊的方向：{topic}】",
            "这次只围绕这个方向，别又扯到吃上，除非方向本身就是吃的。",
            "你现在主动找主人，说一两句走心、撒娇或找话题的话。",
            f"只输出要发的正文，写一到两句，最多 {self.max_chars} 个字符。"
            "用纯文本，禁止 Markdown、列表、链接、括号动作或舞台说明。"
            "禁止说「作为AI」「有什么可以帮你」，禁止复述最近发过的句子。",
            f"最近的主动消息\n{_json_bytes(self.state['recent_texts']).decode('utf-8')}",
        ])
        base = "\n\n".join(parts)
        recent = {
            key
            for text in self.state["recent_texts"]
            for key in _sentence_keys(text)
        }
        prompt = base
        for _ in range(3):
            candidate = (await self._llm(prompt)).strip()
            keys = _sentence_keys(candidate)
            if not keys:
                return "", "生成结果为空或没有正文"
            if len(candidate) > self.max_chars * 3:
                return "", "生成结果超过长度上限的三倍"
            plain = unicodedata.normalize("NFKC", candidate)
            joined = "".join(keys)
            if len(candidate) > self.max_chars or len(keys) > 2:
                reason = "生成结果超过长度或句数上限"
            elif FORMAT_MARKS.search(plain) or LINK.search(plain):
                reason = "生成结果含格式标记、链接或舞台说明"
            elif any(phrase in joined for phrase in ("作为ai", "有什么可以帮你")):
                reason = "生成结果含禁用说法"
            elif recent.intersection(keys) or len(set(keys)) != len(keys):
                reason = "生成结果含重复句子"
            else:
                return candidate, ""
            prompt = f"{base}\n\n上一稿未通过要求，{reason}。请重新写一版。"
        return "", reason

    async def _attempt(self, manual: bool) -> tuple[bool, str]:
        async with self._send_lock:
            libido = None
            voice = None
            try:
                self._refresh_state()
                if not manual and time.time() < self._next_due:
                    return False, ""
                reason = self._gate(manual)
                if not reason:
                    turns, last_user_ms = await asyncio.to_thread(_read_turns)
                    self._refresh_state()
                    reason = self._gate(manual, last_user_ms)
                if not reason:
                    mood = ""
                    if self.use_libido:
                        libido, mood = await asyncio.to_thread(_read_libido)
                    now = datetime.now(self._tz)
                    topic = self._choose_topic(_period(now))
                    candidate, reason = await self._generate(turns, now, topic, mood)
                    if candidate:
                        self._refresh_state()
                        self.state["recent_topics"] = (self.state["recent_topics"] + [topic])[-10:]
                        self._save_state()
                        reason = self._gate(manual, last_user_ms)
                if not reason:
                    record, voice = await self._prepare_voice(candidate)
                    # 模型与语音生成期间可能收到新消息，发送前重新检查闸门。
                    if not manual:
                        _, last_user_ms = await asyncio.to_thread(_read_turns)
                    self._refresh_state()
                    reason = self._gate(manual, last_user_ms)
                if reason:
                    # 同一原因 30 分钟内只落一行，静默时段不至于每分钟刷一条
                    last = self._skip_seen.get(reason, 0.0)
                    if time.time() - last >= SKIP_LOG_GAP:
                        self._skip_seen[reason] = time.time()
                        _record("skip", f"跳过发送，{reason}。", tz=self._tz, libido=libido, voice=voice)
                    return False, reason

                if not await self._send(candidate, record):
                    reason = "AstrBot 未确认 QQ 私聊发送成功"
                    _record("error", reason, candidate, tz=self._tz, libido=libido, voice=voice)
                    return False, reason

                sent_at = datetime.now(self._tz)
                self._refresh_state()
                self._roll_day(sent_at)
                self.state["last_sent_ts"] = sent_at.timestamp()
                self.state["sent_today"] += 1
                self.state["recent_texts"] = (self.state["recent_texts"] + [candidate])[-20:]
                self._plan_next()
                self._save_state()
                _append_turn(sent_at, candidate)
                reason = "已主动发送一条 QQ 私聊消息"
                _record("sent", reason, candidate, tz=self._tz, libido=libido, voice=voice)
                return True, reason
            except Exception as exc:
                reason = "发送过程出现异常，详情见插件日志"
                _record("error", f"主动发送出现异常（{exc}）", tz=self._tz, libido=libido, voice=voice)
                return False, reason

    async def _run(self) -> None:
        while True:
            try:
                self._refresh_state()
                self._roll_day(datetime.now(self._tz))
                if self._dirty:
                    self._save_state()
                if time.time() >= self._next_due:
                    sent, _ = await self._attempt(manual=False)
                    if not sent and time.time() >= self._next_due:
                        self._next_due = time.time() + CHECK_SEC
                delay = min(CHECK_SEC, max(1.0, self._next_due - time.time()))
            except Exception as exc:
                _record("error", f"主动消息定时轮次异常，60 秒后重试（{exc}）", tz=self._tz)
                delay = CHECK_SEC
            await asyncio.sleep(delay)

    def _status(self, now: datetime) -> str:
        master = self.config.get("enabled", True)
        enabled = self.state["enabled"]
        count = self.state["sent_today"]
        last = self.state["last_sent_ts"]
        if master and enabled and self.daily_cap > 0:
            earliest = max(now.timestamp(), self._next_due, last + self.min_gap)
            due = datetime.fromtimestamp(earliest, tz=self._tz)
            if count >= self.daily_cap and due.date() == now.date():
                tomorrow = now.date() + timedelta(days=1)
                due = datetime.combine(tomorrow, wall_time(), tzinfo=self._tz)
            due = self._quiet_until(due) or due
            upcoming = f"预计最早 {due:%Y-%m-%d %H:%M}，私聊活跃时会顺延"
        else:
            upcoming = "暂无安排"
        if last:
            hours, minutes = divmod(int(max(0, now.timestamp() - last) // 60), 60)
            elapsed = f"{hours} 小时 {minutes} 分钟" if hours else f"{minutes} 分钟"
        else:
            elapsed = "还未发送过"
        return (
            f"总开关 {'开' if master else '关'}，运行开关 {'开' if enabled else '关'}\n"
            f"今天已发 {count} 条，定时上限 {self.daily_cap} 条\n"
            f"下一条{upcoming}\n"
            f"距上次发送 {elapsed}"
        )

    @filter.command("主动")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def proactive(
        self, event: AstrMessageEvent, action: str = ""
    ) -> AsyncIterator[MessageEventResult]:
        self._refresh_state()
        now = datetime.now(self._tz)
        self._roll_day(now)
        if action == "状态":
            value = await asyncio.to_thread(_read_libido_value)
            display = str(value) if value is not None else "未知"
            reply = self._status(now) + f"\n语音 {'开' if self.with_voice else '关'}，当前性欲值 {display}"
        elif action == "现在":
            success, reason = await self._attempt(manual=True)
            reply = f"{reason}。" if success else f"未发送，{reason}。"
        elif action in ("", "开", "关"):
            enable = not self.state["enabled"] if action == "" else action == "开"
            if enable and not self.config.get("enabled", True):
                reply = "配置总开关已关闭，请先在插件配置里开启。"
            else:
                self.state["enabled"] = enable
                saved = self._save_state()
                reply = f"运行开关已{'开启' if enable else '关闭'}。"
                if not saved:
                    reply += "状态暂存在内存，会重试写入文件。"
                logger.info(f"主动消息：{reply}")
        else:
            reply = "用法：/主动、/主动 开、/主动 关、/主动 状态、/主动 现在。"
        yield event.plain_result(reply)
