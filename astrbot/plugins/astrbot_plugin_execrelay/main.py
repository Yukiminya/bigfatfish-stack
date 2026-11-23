"""开发期执行中继：读 /android-share/exec.json，在 AstrBot 进程同一个 Python 环境里跑命令/代码，结果写 /android-share/exec_res.json。

只给开发调试用，配好语音识别后卸载。
"""

import asyncio
import json
import os
import subprocess
import sys

from astrbot.api import logger
from astrbot.api.star import Context, Star, register

_REQ = "/android-share/exec.json"
_RES = "/android-share/exec_res.json"


def _load_req():
    try:
        with open(_REQ, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _run(req: dict) -> dict:
    timeout = int(req.get("timeout") or 120)
    python = str(req.get("py") or "")
    cmd = str(req.get("cmd") or "")
    cwd = req.get("cwd") or "/persistent/astrbot"
    try:
        if python:
            argv = [sys.executable, "-c", python]
        else:
            argv = cmd
        proc = subprocess.run(
            argv,
            shell=not python,
            capture_output=True,
            timeout=timeout,
            cwd=cwd if os.path.isdir(str(cwd)) else None,
        )
        return {
            "rc": proc.returncode,
            "out": proc.stdout.decode("utf-8", "replace")[:200000],
            "err": proc.stderr.decode("utf-8", "replace")[:20000],
            "python": sys.executable,
        }
    except Exception as exc:  # noqa: BLE001
        return {"rc": -1, "out": "", "err": f"{type(exc).__name__}: {exc}", "python": sys.executable}


@register("astrbot_plugin_execrelay", "dev", "开发期执行中继", "v1.0.0")
class ExecRelay(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.context = context
        try:
            asyncio.get_running_loop().create_task(self._loop())
        except RuntimeError:
            import threading

            threading.Thread(target=lambda: asyncio.run(self._loop()), daemon=True).start()

    async def _loop(self) -> None:
        last_seq = None
        while True:
            await asyncio.sleep(0.5)
            req = _load_req()
            if not req:
                continue
            seq = req.get("seq")
            if seq is not None and seq == last_seq:
                continue
            last_seq = seq
            payload = await asyncio.to_thread(_run, req)
            payload["seq"] = seq
            try:
                with open(_RES, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False)
            except OSError:
                logger.warning("execrelay 写回失败")
