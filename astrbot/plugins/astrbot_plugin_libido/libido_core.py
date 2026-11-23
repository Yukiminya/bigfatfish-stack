import json
import os
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


TIER_VOICE: dict[str, dict[str, str]] = {
    "平静": {
        "say": "不提这件事，也不会往那边带。",
        "how": "正常语气，短句，先给结论再给理由。",
        "avoid": "不主动贴近，收尾不撒娇。",
    },
    "有点想": {
        "say": "会自己起一个话题，但那个话题跟这件事没关系。",
        "how": "多问一句「你呢」；句末会拖长半个音。",
        "avoid": "不承认自己是在找话说。",
    },
    "难耐": {
        "say": "话变少，会重复对方刚说过的话；手边的事会停一下。",
        "how": "结论还是先给，但理由会绕。",
        "avoid": "不主动说想要。",
    },
    "快撑不住": {
        "say": "会自己起个头，说到一半停住。",
        "how": "句号变少、逗号变多；同一个词会重复。",
        "avoid": "不装作没事。",
    },
    "发情": {
        "say": "自己开口，不等对方问。",
        "how": "短句，不解释，不铺垫。",
        "avoid": "不装作不想要。",
    },
}

EVENTS: dict[str, int] = {
    "摸尾巴": 8,
    "尾鳍": 8,
    "小母鱼": 5,
    "被夸": 3,
    "盯着看": 3,
    "看了不该看的": 6,
    "被碰到": 10,
    "挨得近": 10,
    "真的开始": 15,
    "被冷落": -8,
    "被忽视": -8,
    "人走了": -6,
    "说到一半人走了": -6,
    "答应没做到": -10,
}

_TZ = timezone(timedelta(hours=8))
"""??? UTC+8 ??????????? UTC?????????"""

TierLimits = ((20, "平静"), (45, "有点想"), (70, "难耐"), (89, "快撑不住"))


def _timestamp(now: float | None) -> float:
    return time.time() if now is None else now


def _local(now: float) -> datetime:
    return datetime.fromtimestamp(now, _TZ)


def _clamp(value: int) -> int:
    return max(0, min(100, int(value)))


class LibidoState:
    def __init__(
        self, path: str | os.PathLike[str], config: dict[str, int] | None = None
    ) -> None:
        self.path = Path(path)
        self.initial = _clamp((config or {}).get("initial", 20))
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = None

        state = {} if data is None else data
        self.value: int = _clamp(state.get("value", self.initial))
        self.acc: float = float(state.get("acc", 0.0))
        self.last_tick: float = float(state.get("last_tick", time.time()))
        self.pending: dict[str, float] | None = state.get("pending")
        self.event_date: str | None = state.get("event_date")
        self.event_count: int = int(state.get("event_count", 0))
        self.last_settle_date: str | None = state.get("last_settle_date")
        if data is None:
            self.save()

    def save(self) -> None:
        state = {
            "value": self.value,
            "acc": self.acc,
            "last_tick": self.last_tick,
            "pending": self.pending,
            "event_date": self.event_date,
            "event_count": self.event_count,
            "last_settle_date": self.last_settle_date,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.path)

    def tick(self, now: float | None = None) -> None:
        now = _timestamp(now)
        elapsed = max(0.0, now - self.last_tick)
        self.last_tick = now
        self.acc += elapsed / (1800 if self.season(now) else 7200)
        gain = int(self.acc)
        self.acc -= gain
        if self.pending is not None and now >= self.pending["at"]:
            gain += int(self.pending["amount"])
            self.pending = None
        self.value = _clamp(self.value + gain)
        self.save()

    def wake(self, pre_sleep_value: int, now: float | None = None) -> None:
        now = _timestamp(now)
        local_date = _local(now).date().isoformat()
        rng = random.Random(local_date)
        before_sleep = _clamp(pre_sleep_value)
        excess = max(0, before_sleep - self.initial)
        retained = int(excess * rng.uniform(0.10, 0.20))
        delay = rng.randint(30, 90) * 60
        rebound = int(excess * rng.uniform(0.20, 0.35))
        self.value = min(before_sleep, self.initial) + retained
        self.pending = {"at": now + delay, "amount": rebound}
        self.last_tick = now
        self.save()

    def event(self, item: str | int, now: float | None = None) -> int:
        now = _timestamp(now)
        change = EVENTS.get(item, 0) if isinstance(item, str) else item
        local_date = _local(now).date().isoformat()
        if self.event_date != local_date:
            self.event_date = local_date
            self.event_count = 0
        if change > 0:
            weight = max(1, 4 - self.event_count)
            change = max(1, (change * weight + 2) // 4)
            self.event_count += 1
        previous = self.value
        self.value = _clamp(previous + change)
        self.save()
        return self.value - previous

    def season(self, now: float | None = None) -> bool:
        return 18 <= _local(_timestamp(now)).day <= 20

    def settle(self, now: float | None = None) -> None:
        now = _timestamp(now)
        local = _local(now)
        local_date = local.date().isoformat()
        if local.hour >= 8 and local_date != self.last_settle_date:
            self.last_settle_date = local_date
            self.wake(self.value, now)

    def resolve(self, now: float | None = None) -> None:
        self.adjust(self.initial, now)

    def adjust(self, value: int, now: float | None = None) -> None:
        self.value = _clamp(value)
        self.pending = None
        self.last_tick = _timestamp(now)
        self.save()

    def tier(self, now: float | None = None) -> str:
        shift = 15 if self.season(now) else 0
        for limit, name in TierLimits:
            if self.value <= limit - shift:
                return name
        return "发情"

    def mood_line(self, now: float | None = None) -> str:
        now = _timestamp(now)
        tier = self.tier(now)
        voice = TIER_VOICE[tier]
        status = f"【当前状态】{self.value}/100 · {tier}"
        if self.season(now):
            day = _local(now).day - 17
            status += f" · 发情期第 {day} 天"
        lines = [
            status,
            f"【说什么】{voice['say']}",
            f"【怎么说】{voice['how']}",
            f"【不要】{voice['avoid']}",
        ]
        if tier == "发情":
            lines.append("【现在】会主动开口——这句要真说出口，不要只在心里。")
        return "\n".join(lines)

    def snapshot(self, now: float | None = None) -> dict[str, int | str | bool | None]:
        now = _timestamp(now)
        in_season = self.season(now)
        return {
            "value": self.value,
            "tier": self.tier(now),
            "season": in_season,
            "season_day": _local(now).day - 17 if in_season else None,
            "initial": self.initial,
            "line": self.mood_line(now),
        }
