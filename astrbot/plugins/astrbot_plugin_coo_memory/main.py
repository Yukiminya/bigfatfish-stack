import asyncio
import json
import os
import tempfile
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register


QQ_PLATFORMS = {"aiocqhttp", "qq_official", "qq_official_webhook"}
MEMORY_INTRO = "下面这段是你在电脑上那个分身（桌宠 Coo）记着的事，你俩共用一份记忆，可以自然地用上：\n"
# 每次都跟一句能力事实：它以前总说「语音还没装」，其实插件早就在发语音条了
VOICE_FACT = (
    "（关于你自己的嗓子：你已经能发语音了。你发出去的每条文字回复会自动附上一条语音条，"
    "声音是在电脑上用你的音色合成好再发出去的，不需要你做任何操作，也不要去看什么后台。"
    "AstrBot 设置里那个 TTS /provider 栏是空的，跟你的嗓子无关，别拿它当证据。"
    "只有主人（641961016）在私聊里能收到语音条，群里和别人的对话不带语音。"
    "所以别再说「语音还没装」「我只能打字」「TTS 那栏是空的」这类话；宝宝说听到了语音，那就是你在出声。）\n"
)
VOICE_FACT_TAIL = (
    "（提醒：语音条是插件在你回复后自动附带的，你什么都不用做。"
    "AstrBot 后台 TTS 设置空着跟这事无关，别再翻后台、也别再说自己没声——"
    "主人刚刚已经收到过你发的语音条了。）"
)


@register("astrbot_plugin_coo_memory", "phone-hands", "和电脑上的桌宠共享记忆", "v1.0.0")
class CooMemory(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        cfg = config or {}
        self._enabled = cfg.get("enable", True)
        self._inject = cfg.get("inject", True)
        self._record = cfg.get("record", True)
        self._share = Path(cfg.get("share_dir", "/android-share"))
        self._max_chars = max(0, int(cfg.get("max_inject_chars", 2500)))
        self._queue = self._share / "qq-turns.jsonl"
        self._lock = threading.Lock()
        self._last_seq = 0
        self._line_count = None
        self._needs_newline = False
        self._memory_stamp = None
        self._memory_text = ""
        self._next_check = 0.0

    def _for_qq(self, event):
        return self._enabled and event.get_platform_name() in QQ_PLATFORMS

    def _scan_queue(self):
        count = 0
        needs_newline = False
        try:
            with open(self._queue, "r", encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    count += 1
                    needs_newline = not line.endswith("\n")
                    try:
                        seq = json.loads(line)["seq"]
                        if type(seq) is int:
                            self._last_seq = max(self._last_seq, seq)
                    except (ValueError, TypeError, KeyError):
                        logger.warning("共享记忆跳过无效队列行 %d", count)
        except FileNotFoundError:
            pass
        self._line_count = count
        self._needs_newline = needs_newline

    def _append(self, role, name, text):
        with self._lock:
            self._share.mkdir(parents=True, exist_ok=True)
            if self._line_count is None:
                self._scan_queue()

            # 使用毫秒序号，并避开同一毫秒的并发消息与手机时钟回拨。
            seq = max(time.time_ns() // 1_000_000, self._last_seq + 1)
            self._last_seq = seq
            row = {
                "seq": seq,
                "t": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "role": role,
                "name": name,
                "text": text,
            }
            prefix = "\n" if self._needs_newline else ""
            try:
                with open(self._queue, "a", encoding="utf-8", newline="\n") as stream:
                    stream.write(prefix + json.dumps(row, ensure_ascii=False) + "\n")
            except Exception:
                self._line_count = None
                raise
            self._needs_newline = False
            self._line_count += 1

            if self._line_count > 5000:
                with open(self._queue, "r", encoding="utf-8") as stream:
                    newest = deque(stream, maxlen=2000)
                # 同目录替换，让 ADB 看到轮转前或轮转后的完整文件。
                with tempfile.TemporaryDirectory(dir=self._share) as folder:
                    temp = Path(folder) / self._queue.name
                    with open(temp, "a", encoding="utf-8", newline="\n") as stream:
                        stream.writelines(newest)
                    os.replace(temp, self._queue)
                self._line_count = len(newest)

    def _read_memory(self):
        with self._lock:
            now = time.monotonic()
            if now < self._next_check:
                return self._memory_text
            self._next_check = now + 30
            path = self._share / "pet-memory.md"
            try:
                stat = path.stat()
                stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
                if stamp != self._memory_stamp:
                    with open(path, "r", encoding="utf-8") as stream:
                        self._memory_text = stream.read()
                    self._memory_stamp = stamp
            except FileNotFoundError:
                self._memory_stamp = None
                self._memory_text = ""
            return self._memory_text

    def _recent_turns(self, limit: int = 16) -> str:
        return self._recent_for_prompt(limit, None)

    def _recent_for_prompt(self, limit: int, prompt: str | None) -> str:
        import json
        from collections import deque

        with self._lock:
            try:
                stat = self._queue.stat()
                key = (stat.st_mtime_ns, stat.st_size, limit)
                cached_key, expires, turns = getattr(self, "_recent_cache", (None, 0, []))
                now = time.monotonic()
                if key != cached_key or now >= expires:
                    with self._queue.open(encoding="utf-8") as stream:
                        turns = [json.loads(line) for line in deque(stream, maxlen=limit)]
                    self._recent_cache = (key, now + 30, turns)
            except (OSError, ValueError):
                return ""

        # 比较截断前的原文，仅从本次注入结果移除最后一条匹配的提问。
        if prompt is not None:
            for index, turn in reversed(list(enumerate(turns))):
                if turn["role"] == "user" and turn["text"] == prompt:
                    turns = turns[:index] + turns[index + 1:]
                    break
        return "\n".join(
            f'{turn["name"]}：{turn["text"][:120]}' for turn in turns
            if "语音条是插件在你回复后自动附带的" not in turn["text"]
        )

    def _status(self):
        with self._lock:
            try:
                with open(self._queue, "r", encoding="utf-8") as stream:
                    queued = sum(1 for line in stream if line.strip())
            except FileNotFoundError:
                queued = 0
        return queued, len(self._read_memory())

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req):
        try:
            if not self._for_qq(event):
                return
            recorded_prompt = None
            if self._record and isinstance(req.prompt, str):
                try:
                    await asyncio.to_thread(
                        self._append, "user", event.get_sender_name(), req.prompt
                    )
                    recorded_prompt = req.prompt
                except Exception as exc:
                    logger.warning("共享记忆记录 QQ 提问失败 %r", exc)

            if self._inject:
                memory = (await asyncio.to_thread(self._read_memory)).strip()[: self._max_chars]
                block = VOICE_FACT + (MEMORY_INTRO + memory if memory else "")
                if recorded_prompt:
                    recent = await asyncio.to_thread(
                        self._recent_for_prompt, 16, recorded_prompt
                    )
                else:
                    recent = await asyncio.to_thread(self._recent_turns, 16)
                if recent:
                    block += "\n\n你们最近的对话（含语音通话）：\n" + recent
                req.system_prompt = (req.system_prompt or "") + "\n\n" + block
                if isinstance(req.prompt, str) and req.prompt.strip():
                    # 系统提示词可能被 persona 重建覆盖，用户消息末尾再钉一次最稳
                    req.prompt = req.prompt.rstrip() + "\n\n" + VOICE_FACT_TAIL
                recent = recent[:1500]
                if recent:
                    req.prompt += "\n\n【你们最近的对话，可以在回答里自然用上】\n" + recent

                # 把摘录中的换行转成空格，每次请求只写一行日志。
                try:
                    head = " ".join(recent[:200].splitlines())
                    with open("/android-share/coo_memory_debug.log", "a", encoding="utf-8") as stream:
                        stream.write(
                            f"[{time.strftime('%H:%M:%S')}] recent={len(recent)}字 head={head}\n"
                        )
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("共享记忆处理 LLM 请求失败 %r", exc)

    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent):
        try:
            if not self._for_qq(event) or not self._record:
                return
            # 发送阶段在某些失败后仍会调用钩子，先检查适配器的发送标记。
            if not getattr(event, "_has_send_oper", False):
                return
            result = event.get_result()
            text = result.get_plain_text() if result is not None else ""
            if text.strip():
                await asyncio.to_thread(self._append, "assistant", "雪糕鱼", text)
        except Exception as exc:
            logger.warning("共享记忆记录 QQ 回复失败 %r", exc)

    @filter.command("记忆")
    async def memory_status(self, event: AstrMessageEvent):
        try:
            if not self._for_qq(event):
                return
            queued, chars = await asyncio.to_thread(self._status)
            yield event.plain_result(f"共享记忆：手机 {queued} 条对话排队 / 电脑记忆 {chars} 字")
        except Exception as exc:
            logger.warning("共享记忆查询状态失败 %r", exc)