"""探针：把 AstrBot 的插件钩子、请求对象字段、数据目录和会话库结构写到 /android-share/memprobe.json。"""

import json
import os
import sqlite3

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

SHARE = "/android-share"
DATA = "/persistent/astrbot/data"


def _names(obj):
    try:
        return [n for n in dir(obj) if not n.startswith("_")]
    except Exception as e:  # pragma: no cover
        return repr(e)


def _ls(path, limit=60):
    try:
        return sorted(os.listdir(path))[:limit]
    except Exception as e:
        return repr(e)


def collect():
    out = {}
    try:
        import astrbot

        out["astrbot_file"] = getattr(astrbot, "__file__", None)
        out["astrbot_version"] = getattr(astrbot, "__version__", None)
    except Exception as e:
        out["astrbot_err"] = repr(e)

    out["cwd"] = os.getcwd()
    out["paths"] = {
        "/persistent/astrbot": _ls("/persistent/astrbot"),
        "/persistent/astrbot/data": _ls(DATA),
        "/android-share": _ls(
            "/android-share"
        ),
    }

    try:
        from astrbot.api import provider as prov

        req = getattr(prov, "ProviderRequest", None)
        fields = None
        if req is not None:
            fields = list(getattr(req, "model_fields", {}) or getattr(req, "__dataclass_fields__", {}) or {})
        out["provider_request"] = fields
    except Exception as e:
        out["provider_err"] = repr(e)

    try:
        import astrbot.api.event as ev

        out["filter_names"] = _names(ev.filter)
        out["event_methods"] = [n for n in _names(AstrMessageEvent) if any(k in n.lower() for k in ("text", "message", "session", "conv", "sender", "unified", "group"))]
    except Exception as e:
        out["filter_err"] = repr(e)

    # 会话库
    for name in ("data.db", "astrbot.db", "conversations.db"):
        p = os.path.join(DATA, name)
        if os.path.isfile(p):
            try:
                con = sqlite3.connect(p)
                tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
                out.setdefault("dbs", {})[name] = {
                    "size": os.path.getsize(p),
                    "tables": tables,
                }
                for t in tables:
                    if "conversation" in t.lower() or "session" in t.lower():
                        cols = [r[1] for r in con.execute(f"pragma table_info({t})")]
                        cnt = con.execute(f"select count(*) from {t}").fetchone()[0]
                        out["dbs"][name][t] = {"cols": cols, "rows": cnt}
                con.close()
            except Exception as e:
                out.setdefault("dbs", {})[name] = repr(e)
    return out


@register("astrbot_plugin_memprobe", "phone-hands", "AstrBot 能力探针", "v1.0.1")
class MemProbe(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self._write()

    def _write(self):
        try:
            os.makedirs(SHARE, exist_ok=True)
            with open(os.path.join(SHARE, "memprobe.json"), "w", encoding="utf-8") as f:
                json.dump(collect(), f, ensure_ascii=False, indent=1, default=str)
        except Exception:
            pass

    async def initialize(self):
        self._write()

    @filter.command("memprobe")
    async def memprobe(self, event: AstrMessageEvent):
        self._write()
        yield event.plain_result("probe ok")
