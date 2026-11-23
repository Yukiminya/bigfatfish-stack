import asyncio, json, time
from astrbot.api.star import Context, Star, register

URLS = ["http://127.0.0.1:9880", "http://192.168.1.2:9881"]

def _probe(url):
    import urllib.request
    body = json.dumps({"text": "手机通路测试，一二三。"}).encode()
    t0 = time.time()
    try:
        req = urllib.request.Request(url + "/tts", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            data = r.read()
        return {"url": url, "code": r.status, "len": len(data), "riff": data[:4] == b"RIFF", "ms": int((time.time()-t0)*1000)}
    except Exception as exc:
        return {"url": url, "error": f"{type(exc).__name__}: {exc}", "ms": int((time.time()-t0)*1000)}

@register("astrbot_plugin_probe", "lead", "probe", "v1.0.0")
class Probe(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        try:
            asyncio.get_running_loop().create_task(self._run())
        except RuntimeError:
            import threading
            threading.Thread(target=lambda: asyncio.run(self._run()), daemon=True).start()

    async def _run(self):
        await asyncio.sleep(2)
        lines = [json.dumps(await asyncio.to_thread(_probe, u), ensure_ascii=False) for u in URLS]
        try:
            with open("/android-share/probe.txt", "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception:
            pass
