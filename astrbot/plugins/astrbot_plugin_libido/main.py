import asyncio
import json
import os
import re
import sys
import threading
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryFile
from typing import Any
from urllib.parse import urlsplit

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from quart import Response, jsonify, request

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from libido_core import LibidoState, EVENTS  # noqa: E402


ROOT = "/astrbot_plugin_libido"
DATA_DIR = Path("/persistent/astrbot/data/plugin_data/astrbot_plugin_libido")
STATE_FILE = "发情状态.json"

PANEL = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>性欲面板</title>
<style>
:root{color-scheme:dark;font-family:system-ui,sans-serif}
body{margin:0;padding:1rem;min-height:90vh;display:grid;place-items:center;background:#11121a;color:#f8f3ff}
main{width:min(100%,28rem);box-sizing:border-box;padding:1.5rem;background:#1d2030;border:1px solid #373b53;border-radius:1.25rem}
h1{font-size:1rem;margin:0 0 1rem;color:#ddd0ef}
strong{font-size:clamp(2.5rem,12vw,4.5rem);font-variant-numeric:tabular-nums}
progress{width:100%;height:1rem;accent-color:#cb9af9}
.actions{display:grid;grid-template-columns:repeat(2,1fr);gap:.7rem;margin-top:1rem}
button{min-height:3rem;border:1px solid #64557a;border-radius:.7rem;background:#332943;color:inherit;font:inherit;cursor:pointer}
button:disabled{opacity:.55}
#status{min-height:1.5em;margin-bottom:0;color:#ffd0c8}
</style>
</head>
<body><main>
<h1>性欲面板</h1>
<strong id="value">—/100</strong>
<p><span id="tier">—</span> · <span id="day">—</span></p>
<progress id="bar" max="100" aria-label="当前数值"></progress>
<div class="actions">
<button data-action="wake">睡醒</button><button data-action="resolve">解决</button>
<button data-event="-8">-8</button><button data-event="8">+8</button>
</div>
<p id="status" role="status"></p>
</main>
<script>
const $ = id => document.getElementById(id);
const buttons = document.querySelectorAll('button');
const base = '';
let revision = 0;
let busy = false;

function render(s) {
  $('value').textContent = `${s.value ?? '—'}/100`;
  $('bar').value = s.value ?? 0;
  $('tier').textContent = s.tier ?? '—';
  const day = s.season_day;
  $('day').textContent = day != null ? `发情期第 ${day} 天` : '不在发情期';
}
async function fetchState(path, options) {
  const response = await fetch(base + path, {cache: 'no-store', ...options});
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}
async function refresh() {
  if (busy) return;
  const current = ++revision;
  try {
    const state = await fetchState('/api/state');
    if (current === revision) render(state);
  } catch {
    if (current === revision) $('status').textContent = '刷新失败，稍后重试';
    return;
  }
  if (current === revision) $('status').textContent = '';
}
for (const button of buttons) button.onclick = async () => {
  if (busy) return;
  busy = true;
  ++revision;
  for (const item of buttons) item.disabled = true;
  try {
    const {action, event} = button.dataset;
    render(await fetchState('/api/mood', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(action ? {action} : {event: Number(event)})
    }));
    $('status').textContent = '';
  } catch {
    $('status').textContent = '操作失败，请查看当前状态';
  } finally {
    busy = false;
    for (const item of buttons) item.disabled = false;
  }
};
refresh();
setInterval(refresh, 5000);
</script></body></html>"""


class _PanelHandler(BaseHTTPRequestHandler):
    """面板用的独立小服务：不走 AstrBot 后台，手机浏览器直接开就行。"""

    plugin: Any = None

    def log_message(self, *_args: Any) -> None:
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, data: Any) -> None:
        self._send(
            code,
            json.dumps(data, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        try:
            if path in ("/", "/index.html", "/panel"):
                self._send(200, PANEL.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/state":
                self._json(200, self.plugin._snapshot())
            elif path == "/api/mood-line":
                self._send(
                    200, self.plugin._line().encode("utf-8"), "text/plain; charset=utf-8"
                )
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        if urlsplit(self.path).path != "/api/mood":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(body, dict):
                raise ValueError("需要一个 JSON 对象")
        except Exception as exc:  # noqa: BLE001
            self._json(400, {"error": f"请求体不合法：{exc}"})
            return
        try:
            self._json(200, self.plugin._apply(body))
        except Exception as exc:  # noqa: BLE001
            self._json(400, {"error": str(exc)})


@register(
    "astrbot_plugin_libido",
    "Codex",
    "给大肥鱼加一个会自己走的性欲数值和档位，档位决定语气",
    "v1.0.0",
)
class LibidoPlugin(Star):
    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context)
        settings = dict(config) if config is not None else {}
        self.enabled = bool(settings.get("enabled", True))
        try:
            self._tick_seconds = max(30, int(settings.get("tick_seconds", 300)))
        except (TypeError, ValueError):
            self._tick_seconds = 300
        self.state: LibidoState | None = None
        self._lock = threading.Lock()
        self._task: asyncio.Task[None] | None = None
        self._enabled_path: Path | None = None

        for directory in (DATA_DIR, Path(__file__).resolve().parent):
            path = directory / STATE_FILE
            try:
                directory.mkdir(parents=True, exist_ok=True)
                with TemporaryFile(dir=directory):
                    pass
                if path.exists():
                    with path.open("ab"):
                        pass
                self.state = LibidoState(str(path), settings)
                self._enabled_path = directory / "enabled.json"
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"加载性欲状态失败（{directory}）：{exc}")

        try:
            if self._enabled_path is not None and self._enabled_path.exists():
                saved = json.loads(self._enabled_path.read_text(encoding="utf-8"))
                enabled = saved["enabled"]
                if not isinstance(enabled, bool):
                    raise ValueError("enabled 必须是布尔值")
                self.enabled = enabled
        except Exception as exc:  # noqa: BLE001
            self.enabled = bool(settings.get("enabled", True))
            logger.warning(f"读取性欲开关失败：{exc}")

        for route, view, method in (
            ("state", self._api_state, "GET"),
            ("line", self._api_line, "GET"),
            ("mood", self._api_mood, "POST"),
            ("panel", self._api_panel, "GET"),
        ):
            try:
                self.context.register_web_api(
                    f"{ROOT}/{route}", view, [method], f"libido {route}"
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"注册性欲接口 {route} 失败：{exc}")

        with suppress(RuntimeError):
            self._task = asyncio.get_running_loop().create_task(self._run())

        self._panel_server: ThreadingHTTPServer | None = None
        try:
            self._panel_port = int(settings.get("panel_port", 41888))
        except (TypeError, ValueError):
            self._panel_port = 41888
        try:
            _PanelHandler.plugin = self
            self._panel_server = ThreadingHTTPServer(
                ("127.0.0.1", self._panel_port), _PanelHandler
            )
            self._panel_server.daemon_threads = True
            threading.Thread(
                target=self._panel_server.serve_forever, daemon=True
            ).start()
            logger.info(f"性欲面板已启动：http://127.0.0.1:{self._panel_port}/")
        except OSError as exc:
            logger.warning(f"性欲面板端口 {self._panel_port} 起不来：{exc}")
            self._panel_server = None

    async def _run(self) -> None:
        """每 5 分钟结算一次睡醒与时间增长。"""
        while True:
            await asyncio.sleep(self._tick_seconds)
            try:
                if self.state is not None:
                    self.state.settle()
                    self.state.tick()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"更新性欲状态失败：{exc}")

    async def terminate(self) -> None:
        if self._panel_server is not None:
            self._panel_server.shutdown()
            self._panel_server.server_close()
            self._panel_server = None
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    def _require_state(self) -> LibidoState:
        if self.state is None:
            raise RuntimeError("性欲状态未加载")
        return self.state

    def _snapshot(self) -> dict[str, Any]:
        """面板 GET /api/state：先推进时间，再给快照。"""
        state = self._require_state()
        with self._lock:
            state.settle()
            state.tick()
            return state.snapshot()

    def _line(self) -> str:
        with self._lock:
            return self._require_state().mood_line()

    def _apply(self, body: dict[str, Any]) -> dict[str, Any]:
        """面板 POST /api/mood：action / value / event 三选一。"""
        state = self._require_state()
        with self._lock:
            action = body.get("action")
            value = body.get("value")
            item = body.get("event")
            if action == "wake":
                state.wake(state.value)
            elif action == "resolve":
                state.resolve()
            elif isinstance(value, int) and not isinstance(value, bool):
                state.adjust(value)
            elif isinstance(item, str) or isinstance(item, int):
                state.event(item)
            else:
                raise ValueError("无效的 action、value 或 event")
            return state.snapshot()

    def _set_enabled(self, enabled: bool) -> None:
        path = self._enabled_path
        if path is None:
            raise RuntimeError("开关存储目录不可用")
        temp_path = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False
            ) as file:
                temp_path = Path(file.name)
                json.dump({"enabled": enabled}, file)
            os.replace(temp_path, path)
            self.enabled = enabled
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    @filter.on_llm_request()
    async def on_req(self, event: AstrMessageEvent, req: Any) -> None:
        """把「当前状态」那段提示词塞进系统提示词。"""
        try:
            if self.enabled and self.state is not None:
                req.system_prompt = (
                    (req.system_prompt or "") + "\n\n" + self.state.mood_line()
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"追加性欲语气失败：{exc}")

    @filter.command("性欲")
    async def libido(self, event: AstrMessageEvent, arg: str = ""):
        try:
            state = self._require_state()
            text = (arg or "").strip()
            if not text:
                match = re.fullmatch(
                    r"/?性欲(?:\s+(.+))?", event.message_str.strip(), re.DOTALL
                )
                text = (match.group(1) or "").strip() if match else ""
            message = ""
            if not text:
                message = state.mood_line()
            elif text in ("开", "关"):
                self._set_enabled(text == "开")
                message = "已开启" if self.enabled else "已关闭"
            elif text == "面板":
                message = f"手机浏览器打开 http://127.0.0.1:{self._panel_port}/"
            elif text == "睡醒":
                state.wake(state.value)
                message = "睡醒后"
            elif text == "解决":
                state.resolve()
            elif match := re.fullmatch(r"设为\s*([+-]?\d+)", text):
                state.adjust(int(match.group(1)))
            elif re.fullmatch(r"[+-]\d+", text):
                state.event(int(text))
            elif text in EVENTS:
                state.event(text)
            else:
                message = "未知事件。可用事件：" + "、".join(EVENTS)
            state.save()
            status = f"{state.value}/100 · {state.tier()}"
            yield event.plain_result(f"{message}\n{status}" if message else status)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"执行性欲命令失败：{exc}")
            yield event.plain_result("性欲命令执行失败")

    async def _api_state(self) -> Any:
        try:
            return jsonify(self._require_state().snapshot())
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"读取性欲状态失败：{exc}")
            return jsonify(error="状态不可用"), 500

    async def _api_line(self) -> Response:
        try:
            return Response(
                self._require_state().mood_line(),
                content_type="text/plain; charset=utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"读取性欲语气失败：{exc}")
            return Response(
                "状态不可用", status=500, content_type="text/plain; charset=utf-8"
            )

    async def _api_mood(self) -> Any:
        try:
            body = await request.get_json(silent=True)
            if not isinstance(body, dict):
                return jsonify(error="需要 JSON 对象"), 400
            state = self._require_state()
            action = body.get("action")
            value = body.get("value")
            item = body.get("event")
            if action == "wake":
                state.wake(state.value)
            elif action == "resolve":
                state.resolve()
            elif isinstance(value, int) and not isinstance(value, bool):
                state.adjust(value)
            elif isinstance(item, str) or isinstance(item, int):
                state.event(item)
            else:
                return jsonify(error="无效的 action、value 或 event"), 400
            return jsonify(state.snapshot())
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"修改性欲状态失败：{exc}")
            return jsonify(error="操作失败"), 500

    async def _api_panel(self) -> Response:
        return Response(PANEL, content_type="text/html; charset=utf-8")
