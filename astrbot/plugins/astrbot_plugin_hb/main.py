import asyncio, datetime, traceback
from astrbot.api.star import Context, Star, register

@register("astrbot_plugin_hb", "lead", "heartbeat", "v1.0.0")
class HB(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        try:
            with open("/android-share/hb.txt", "a", encoding="utf-8") as f:
                f.write("init " + datetime.datetime.now().isoformat() + "\n")
        except Exception:
            pass
        try:
            asyncio.get_running_loop().create_task(self._loop())
        except RuntimeError:
            import threading
            threading.Thread(target=lambda: asyncio.run(self._loop()), daemon=True).start()

    async def _loop(self):
        n = 0
        while True:
            await asyncio.sleep(2)
            n += 1
            try:
                with open("/android-share/hb.txt", "a", encoding="utf-8") as f:
                    f.write(f"tick {n}\n")
            except Exception:
                pass
