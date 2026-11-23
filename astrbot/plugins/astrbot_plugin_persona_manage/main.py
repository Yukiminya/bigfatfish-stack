import asyncio
import hashlib
import os
import re
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from uuid import uuid4

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star, register
from astrbot.api.web import error_response, json_response, request
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path
from starlette.responses import HTMLResponse

NAME = "astrbot_plugin_persona_manage"
ROOT = f"/{NAME}"
LIMIT = 200 * 1024
KEEP = 50
STAMP = re.compile(r"[0-9]{8}T[0-9]{12}Z-[0-9a-f]{32}")
HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
                 r"\U00020000-\U0002ee5f\U0002f800-\U0002fa1f\U00030000-\U000323af]")
NEWLINE = re.compile(r"\r\n|\r|\n")
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
USAGE = "用法：/人设 查看 [id]｜设置 <文本>｜追加 <文本>｜历史 [id]｜回滚 [id] <ts>"


class InputError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def web(view):
    @wraps(view)
    async def guarded(self):
        try:
            if not request.username:
                raise InputError("请先登录 AstrBot 管理后台。", 401)
            result = await view(self)
            if isinstance(result, HTMLResponse):
                return result
            return json_response(result, headers=HEADERS)
        except InputError as exc:
            return error_response(str(exc), status_code=exc.status, headers=HEADERS)
        except Exception as exc:
            logger.warning(f"[{NAME}] {view.__name__} 失败 ({type(exc).__name__})")
            return error_response("操作失败，请查看 AstrBot 日志。",
                                  status_code=500, headers=HEADERS)
    return guarded


def validate_prompt(prompt):
    if not isinstance(prompt, str):
        raise InputError("system_prompt 必须是字符串。")
    try:
        size = len(prompt.encode("utf-8"))
    except UnicodeEncodeError:
        raise InputError("提示词含有无效的 Unicode 字符。") from None
    if size > LIMIT:
        raise InputError("提示词超过 200 KB（204800 字节）。", 413)
    if not prompt.strip():
        raise InputError("提示词不能为空或全空白。")


def utc_iso(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


@register(NAME, "lead", "管理人设提示词及历史版本", "v1.0.0")
class PersonaManage(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.lock = asyncio.Lock()
        self.history_root = Path(get_astrbot_plugin_data_path()) / NAME / "history"
        for route, view, method, desc in (
            ("panel", self.panel, "GET", "人设管理面板"),
            ("state", self.state, "GET", "读取人设"),
            ("history", self.history, "GET", "读取人设历史"),
            ("save", self.save, "POST", "保存人设"),
            ("rollback", self.rollback, "POST", "回滚人设"),
        ):
            try:
                context.register_web_api(f"{ROOT}/{route}", view, [method], desc)
            except Exception as exc:
                logger.warning(f"[{NAME}] 注册 {route} 失败 ({type(exc).__name__})")

    @property
    def manager(self):
        return self.context.persona_manager

    async def active_id(self):
        return (await self.manager.get_default_persona_v3())["name"]

    async def persona(self, persona_id):
        if not isinstance(persona_id, str) or not persona_id:
            raise InputError("persona_id 必须是非空字符串。")
        try:
            return await self.manager.get_persona(persona_id)
        except ValueError:
            raise InputError("找不到指定的人设。", 404) from None

    def directory(self, persona_id):
        name = persona_id
        if (not re.fullmatch(r"[\w. -]{1,255}", name) or name in {".", ".."}
                or len(name.encode("utf-8")) > 255):
            name = "~" + hashlib.sha256(persona_id.encode("utf-8")).hexdigest()
        directory = self.history_root / name
        if directory.resolve() != directory:
            raise OSError("人设历史路径包含符号链接")
        return directory

    def snapshots(self, directory):
        return sorted(
            (path for path in directory.glob("*.txt")
             if STAMP.fullmatch(path.stem) and not path.is_symlink() and path.is_file()),
            reverse=True,
        )

    def snapshot(self, directory, prompt):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = directory / f"{ts}-{uuid4().hex}.txt"
        temp = path.with_suffix(".tmp")
        try:
            with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                           "wb") as file:
                file.write(prompt.encode("utf-8"))
                file.flush()
                os.fsync(file.fileno())
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)

    def history_rows(self, persona_id):
        rows = []
        for path in self.snapshots(self.directory(persona_id))[:KEEP]:
            prompt = path.read_bytes().decode("utf-8")
            rows.append({"ts": path.stem, "chars": len(prompt), "preview": prompt[:120]})
        return rows

    def read_snapshot(self, directory, ts):
        if not isinstance(ts, str) or not STAMP.fullmatch(ts):
            raise InputError("请使用历史列表中的完整时间戳。")
        path = directory / f"{ts}.txt"
        if path.is_symlink() or not path.is_file():
            raise InputError("找不到指定的历史版本。", 404)
        return path.read_bytes().decode("utf-8")

    async def write(self, persona_id, value, action="save"):
        async with self.lock:
            old = await self.persona(persona_id)
            directory = self.directory(persona_id)
            if action == "rollback":
                value = self.read_snapshot(directory, value)
            elif action == "append":
                validate_prompt(value)
                value = old.system_prompt + ("\n" if old.system_prompt else "") + value
            validate_prompt(value)

            manager = self.manager
            cache = {
                "personas": manager.personas.copy(),
                "personas_v3": manager.personas_v3,
                "persona_v3_config": manager.persona_v3_config,
                "selected_default_persona": manager.selected_default_persona,
                "selected_default_persona_v3": manager.selected_default_persona_v3,
            }
            self.snapshot(directory, old.system_prompt)
            try:
                saved = await manager.update_persona(persona_id, system_prompt=value)
                runtime = manager.get_persona_v3_by_id(persona_id)
                if saved is None or runtime is None or runtime["prompt"] != value:
                    raise RuntimeError("人设更新后未能刷新运行时缓存")
            except (Exception, asyncio.CancelledError):
                try:
                    current = await manager.get_persona(persona_id)
                    if current.system_prompt != old.system_prompt:
                        restored = await manager.db.update_persona(persona_id, system_prompt=old.system_prompt)
                        if restored is None or restored.system_prompt != old.system_prompt:
                            raise RuntimeError("数据库未恢复旧提示词")
                except (Exception, asyncio.CancelledError) as exc:
                    logger.warning(f"[{NAME}] 数据库恢复失败 ({type(exc).__name__})")
                finally:
                    for key, previous in cache.items():
                        setattr(manager, key, previous)
                logger.warning(f"[{NAME}] 人设更新失败，已恢复运行时缓存")
                raise
            finally:
                try:
                    for path in self.snapshots(directory)[KEEP:]:
                        path.unlink()
                except OSError as exc:
                    logger.warning(f"[{NAME}] 清理历史失败 ({type(exc).__name__})")

            return {"status": "ok", "persona_id": persona_id,
                    "updated_at": utc_iso(saved.updated_at)}

    @web
    async def panel(self):
        return HTMLResponse(PANEL, headers={
            **HEADERS,
            "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; "
                                       "style-src 'unsafe-inline'; connect-src 'self'; "
                                       "base-uri 'none'; frame-ancestors 'self'",
        })

    @web
    async def state(self):
        async with self.lock:
            active = await self.active_id()
            selected = request.query.get("persona_id", active)
            personas = await self.manager.get_all_personas()
            persona = await self.persona(selected)
            current = persona if selected == active else await self.persona(active)
            prompt = persona.system_prompt
            chars = len(prompt)
            han = sum(1 for _ in HAN.finditer(prompt))
            return {
                "persona_id": selected,
                "system_prompt": prompt,
                "chars": chars,
                "lines": len(NEWLINE.split(prompt)) if prompt else 0,
                "tokens_est": han + (chars - han + 3) // 4,
                "updated_at": utc_iso(persona.updated_at),
                "active_persona_id": active,
                "active_updated_at": utc_iso(current.updated_at),
                "personas": [{"persona_id": item.persona_id, "chars": len(item.system_prompt)}
                             for item in sorted(personas, key=lambda item: item.persona_id)],
            }

    @web
    async def history(self):
        async with self.lock:
            selected = request.query.get("persona_id")
            persona_id = await self.active_id() if selected is None else selected
            await self.persona(persona_id)
            return self.history_rows(persona_id)

    async def body(self):
        if request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
            raise InputError("写入请求必须来自后台同源页面。", 403)
        media_type = (request.content_type or "").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            raise InputError("请使用 application/json 请求体。")
        data = await request.json()
        if not isinstance(data, dict):
            raise InputError("请求体必须是 JSON 对象。")
        return data

    @web
    async def save(self):
        data = await self.body()
        return await self.write(data.get("persona_id"), data.get("system_prompt"))

    @web
    async def rollback(self):
        data = await self.body()
        return await self.write(data.get("persona_id"), data.get("ts"), "rollback")

    @filter.command("人设")
    async def persona_command(self, event: AstrMessageEvent):
        try:
            args = event.get_message_str().lstrip().removeprefix("/").split(None, 2)
            action = args[1] if len(args) > 1 else ""
            rest = args[2] if len(args) > 2 else ""
            if action in {"设置", "追加", "回滚"} and not event.is_admin():
                raise InputError("只有 AstrBot 管理员可以修改人设。", 403)

            if action == "查看":
                item = await self.persona(rest.strip() or await self.active_id())
                reply = f"人设：{item.persona_id}\n{item.system_prompt}"
            elif action == "历史":
                item = await self.persona(rest.strip() or await self.active_id())
                async with self.lock:
                    rows = self.history_rows(item.persona_id)[:20]
                entries = "\n".join(f"{row['ts']}  {row['chars']} 字符" for row in rows)
                reply = f"人设：{item.persona_id}\n{entries or '暂无历史版本。'}"
            elif action in {"设置", "追加"}:
                result = await self.write(await self.active_id(), rest,
                                          "append" if action == "追加" else "save")
                reply = f"已{action}人设：{result['persona_id']}"
            elif action == "回滚" and rest:
                parts = rest.rsplit(None, 1)
                persona_id = parts[0] if len(parts) == 2 else await self.active_id()
                await self.write(persona_id, parts[-1], "rollback")
                reply = f"已回滚人设：{persona_id}"
            else:
                reply = USAGE
        except InputError as exc:
            reply = str(exc)
        except Exception as exc:
            logger.warning(f"[{NAME}] 聊天命令失败 ({type(exc).__name__})")
            reply = "操作失败，请查看 AstrBot 日志。"
        event.set_result(MessageEventResult().message(reply))


PANEL = r"""<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>人设提示词管理</title>
<style>
:root{color-scheme:light dark;font:16px system-ui}
body{max-width:72rem;margin:auto;padding:1rem}
main{display:grid;grid-template-columns:minmax(10rem,1fr) minmax(0,4fr);gap:1.25rem}
h1,h2,p{margin:.3rem 0 .8rem}
h2{font-size:1.05rem}
select,textarea,button{font:inherit}
select,textarea{display:block;width:100%;box-sizing:border-box;margin:.5rem 0}
textarea{min-height:17rem;height:42vh;resize:vertical;padding:.7rem;font-family:monospace}
button{min-height:2.5rem;padding:.35rem .8rem;cursor:pointer}
li{display:flex;gap:.65rem;align-items:center;justify-content:space-between;
   padding:.3rem 0;border-bottom:1px solid GrayText}
ul{list-style:none;padding:0}
#error{color:light-dark(#a51d20,#ff9b9b)}
#selected,#active{overflow-wrap:anywhere}
@media(max-width:42rem){main{grid-template-columns:minmax(0,1fr);gap:.8rem}}
</style>
<header>
<h1>人设提示词管理</h1>
<p id="active">当前生效（全局默认）</p>
<p id="modified">最近修改时间</p>
</header>
<main>
<aside>
<label for="personas">人设列表</label>
<select id="personas" disabled></select>
</aside>
<section>
<label id="selected" for="prompt">提示词</label>
<textarea id="prompt" spellcheck="false" disabled></textarea>
<p id="counts"></p>
<button id="save" type="button" disabled>保存</button>
<p id="status" role="status"></p>
<p id="error" role="alert"></p>
<h2>最近 20 条历史</h2>
<ul id="history"></ul>
</section>
</main>
<script>
const $ = id => document.getElementById(id);
const select = $('personas'), editor = $('prompt'), save = $('save');
const versionList = $('history'), feedback = $('status'), error = $('error');
const han = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u{20000}-\u{2ee5f}\u{2f800}-\u{2fa1f}\u{30000}-\u{323af}]/gu;
const base = location.pathname.replace(/\/panel\/?$/, '/');
let selected, original = '', normalized = '';
const draft = () => editor.value === normalized ? original : editor.value;
const date = value => new Date(value).toLocaleString('zh-CN');
function count() {
  const value = draft();
  const chars = Array.from(value).length;
  const chinese = (value.match(han) || []).length;
  const lines = value ? value.split(/\r\n|\r|\n/).length : 0;
  $('counts').textContent = chars + ' 字符 / ' + lines + ' 行 / 约 ' +
    (chinese + Math.ceil((chars - chinese) / 4)) + ' token';
}
function snapshotDate(ts) {
  const match = /^(\d{4})(\d\d)(\d\d)T(\d\d)(\d\d)(\d\d)/.exec(ts);
  const [, y, m, d, h, min, s] = match;
  return date(y + '-' + m + '-' + d + 'T' + h + ':' + min + ':' + s + 'Z');
}
async function api(path, body) {
  const options = {credentials: 'same-origin', cache: 'no-store'};
  if (body !== undefined) {
    options.method = 'POST';
    options.headers = {'Content-Type':'application/json'};
    options.body = JSON.stringify(body);
  }
  const response = await fetch(base + path, options);
  const data = await response.json().catch(() => null);
  if (!response.ok || !data || data.status === 'error')
    throw new Error((data && data.message) || ('请求失败（HTTP ' + response.status + '）'));
  return data;
}
async function load(id) {
  const query = id === undefined ? '' : '?persona_id=' + encodeURIComponent(id);
  const state = await api('state' + query);
  selected = state.persona_id;
  select.replaceChildren(...state.personas.map(item =>
    new Option(item.persona_id + '（' + item.chars + ' 字符）', item.persona_id)));
  select.value = selected;
  original = state.system_prompt;
  editor.value = original;
  normalized = editor.value;
  $('selected').textContent = '提示词 · ' + selected;
  $('active').textContent = '当前生效（全局默认） · ' + state.active_persona_id;
  $('modified').textContent = '最近修改时间 · ' + date(state.active_updated_at) +
    (selected !== state.active_persona_id ? ' ｜ 所选人设 · ' + date(state.updated_at) : '');
  count();
  versionList.replaceChildren();
  const versions = await api('history?persona_id=' + encodeURIComponent(selected));
  if (!versions.length) {
    const item = document.createElement('li');
    item.textContent = '暂无历史版本';
    versionList.append(item);
  }
  for (const row of versions.slice(0, 20)) {
    const item = document.createElement('li');
    const label = document.createElement('span');
    label.textContent = snapshotDate(row.ts) + ' · ' + row.chars + ' 字符';
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = '回滚';
    button.onclick = () => run(async () => {
      await api('rollback', {persona_id:selected, ts:row.ts});
      feedback.textContent = '回滚成功';
      await load(selected);
    });
    item.append(label, button);
    versionList.append(item);
  }
}
async function run(operation) {
  feedback.textContent = error.textContent = '';
  select.disabled = editor.disabled = save.disabled = true;
  versionList.inert = true;
  for (const button of versionList.querySelectorAll('button')) button.disabled = true;
  try {
    await operation();
  } catch (err) {
    error.textContent = err.message;
    if (selected !== undefined) select.value = selected;
  } finally {
    select.disabled = editor.disabled = save.disabled = selected === undefined;
    versionList.inert = false;
    for (const button of versionList.querySelectorAll('button')) button.disabled = false;
  }
}
select.onchange = () => run(() => load(select.value));
editor.oninput = count;
save.onclick = () => run(async () => {
  await api('save', {persona_id:selected, system_prompt:draft()});
  feedback.textContent = '保存成功';
  await load(selected);
});
run(() => load());
</script>
</html>
"""
