# backend/weather.py
# 职责：L3 能力层——持久化天气/温度/时间档。
#
# 设计约束：
#   1. 同一天内的天气计划固定，重启不重新随机；
#   2. 只有距上次运行超过 1 天，才重新生成当天计划；
#   3. 天气每天 05:00 / 12:00 / 20:00 三个变化点，可保持不变；
#   4. 雪只出现在 12-07 至 03-05；
#   5. 温度按平滑曲线变化，范围 [-20, 35]；
#   6. 时间档共 9 档，使用 Asia/Shanghai。

from __future__ import annotations

import hashlib
import json
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.config_loader import get

WEATHERS = ["晴", "多云", "阴", "小雨", "中雨", "大雨", "雨夹雪", "小雪", "大雪"]
SNOW_WEATHERS = {"雨夹雪", "小雪", "大雪"}
WEATHER_WEIGHTS = {
    "晴": 30, "多云": 26, "阴": 18, "小雨": 14,
    "中雨": 6, "大雨": 3, "雨夹雪": 2, "小雪": 2, "大雪": 1,
}

# 9 档，小时区间左右闭合；深夜跨午夜
TIME_BANDS = [
    ("深夜", 23, 1),
    ("凌晨", 2, 4),
    ("拂晓", 5, 6),
    ("清晨", 7, 8),
    ("上午", 9, 11),
    ("中午", 12, 14),
    ("下午", 15, 17),
    ("傍晚", 18, 19),
    ("晚上", 20, 22),
]

DEFAULTS = {
    "enabled": True,
    "timezone": "Asia/Shanghai",
    "state_path": "memory/weather_state.json",
    "snow_start": "12-07",
    "snow_end": "03-05",
    "update_hours": [5, 12, 20],
    "temp_min": -20,
    "temp_max": 35,
    "keep_same_chance": 0.42,
    "summer_months": [6, 7, 8],
    "summer_min_temp": 10,
}


def time_band(hour: int) -> str:
    """9 档时间：x:00:00-y:59:59，深夜跨午夜。"""
    for name, start, end in TIME_BANDS:
        if start <= end:
            if start <= hour <= end:
                return name
        elif hour >= start or hour <= end:
            return name
    return "深夜"


def _parse_md(text: str, default: tuple) -> tuple:
    try:
        m, d = str(text).strip().split("-")
        return int(m), int(d)
    except Exception:
        return default


def in_snow_season(day: date) -> bool:
    start = _parse_md(get("weather.snow_start", "12-07"), (12, 7))
    end = _parse_md(get("weather.snow_end", "03-05"), (3, 5))
    md = (day.month, day.day)
    if start <= end:
        return start <= md <= end
    return md >= start or md <= end


class WeatherService:
    def __init__(self, cfg: dict = None, state_path: str = None):
        cfg = dict(DEFAULTS, **(cfg or {}))
        self.cfg = cfg
        self.tz = ZoneInfo(str(cfg.get("timezone") or "Asia/Shanghai"))
        root = Path(__file__).resolve().parent.parent
        p = Path(state_path or cfg.get("state_path") or DEFAULTS["state_path"])
        self.state_path = p if p.is_absolute() else root / p
        self.temp_min = int(cfg.get("temp_min", -20))
        self.temp_max = int(cfg.get("temp_max", 35))
        self.snow_start = _parse_md(cfg.get("snow_start", "12-07"), (12, 7))
        self.snow_end = _parse_md(cfg.get("snow_end", "03-05"), (3, 5))
        self.keep_same = float(cfg.get("keep_same_chance", 0.42))
        self.summer_months = {int(x) for x in (cfg.get("summer_months") or [6, 7, 8])}
        self.summer_min_temp = int(cfg.get("summer_min_temp", 10))
        self.update_hours = [int(x) for x in (cfg.get("update_hours") or [5, 12, 20])]
        self._state = None
        self._snapshot = None
        self._snapshot_key = None

    # ---------- 对外 ----------

    def current(self, now: datetime = None) -> dict:
        now = (now or datetime.now(self.tz)).astimezone(self.tz)
        key = now.strftime("%Y-%m-%d %H:%M")
        if self._snapshot is not None and self._snapshot_key == key:
            return self._snapshot

        state = self._read_state()
        plan, low, high = self._ensure_plan(now, state)
        slot = self._slot(now.hour)
        weather = plan.get(str(slot)) or plan.get("05") or "多云"
        temp = self._temperature(now, low, high, weather)
        snap = {
            "date": now.date().isoformat(),
            "plan_date": (now.date() - timedelta(days=1) if now.hour < 5 else now.date()).isoformat(),
            "weather": weather,
            "temp": temp,
            "time_band": time_band(now.hour),
            "slot": slot,
            "plan": plan,
            "low": low,
            "high": high,
            "last_seen": now.isoformat(timespec="seconds"),
        }
        self._write_state(snap)
        self._snapshot = snap
        self._snapshot_key = key
        return snap

    def summary(self, snap: dict = None) -> str:
        """放入 C1 的天气摘要：只写天气，不写完整日期/温度/时间串。"""
        s = snap or self.current()
        plan = s.get("plan") or {}
        low, high = s.get("low"), s.get("high")
        vals = [plan.get(str(h)) for h in self.update_hours]
        vals = [v for v in vals if v]
        if vals and all(v == vals[0] for v in vals):
            weather = vals[0]
        else:
            weather = " → ".join(v for v in vals if v)
        if low is None or high is None:
            return f"【天气】今天{weather}。"
        return f"【天气】今天{weather}，{low}~{high}°C。"

    def environment(self, snap: dict = None) -> str:
        """放入 D 的当前环境：日期｜天气｜温度｜时间档。"""
        s = snap or self.current()
        return (f"【当前环境】{s['date']}｜{s['weather']}｜"
                f"{s['temp']}°C｜{s['time_band']}")

    # ---------- 状态 ----------

    def _read_state(self) -> dict:
        try:
            if self.state_path.exists():
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _write_state(self, snap: dict) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
            tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(self.state_path)
        except Exception as e:
            print(f"[Weather] 状态写入失败: {e}")

    def _ensure_plan(self, now: datetime, state: dict):
        # 天气日从 05:00 开始；00:00–04:59 仍属于前一天的计划
        weather_day = (now.date() - timedelta(days=1)
                       if now.hour < 5 else now.date())
        plan_date = weather_day.isoformat()
        plan = state.get("plan") if isinstance(state.get("plan"), dict) else None
        same_day = (state.get("plan_date") or state.get("date")) == plan_date and plan
        if same_day:
            # 同一天气日：计划固定，重启也不重算
            low = int(state.get("low", 8))
            high = int(state.get("high", 15))
            # 旧状态迁移：夏季旧计划可能低于硬下限，重算一次
            if weather_day.month in self.summer_months and low < self.summer_min_temp:
                return self._make_plan(weather_day, state)
            return plan, low, high

        # 跨天气日：生成当天计划；距上次运行超过 1 天也走这里
        plan, low, high = self._make_plan(weather_day, state)
        return plan, low, high

    def _in_snow_season(self, day: date) -> bool:
        md = (day.month, day.day)
        start, end = self.snow_start, self.snow_end
        if start <= end:
            return start <= md <= end
        return md >= start or md <= end

    def _make_plan(self, day: date, prev_state: dict):
        seed_text = f"weather-v1:{day.isoformat()}"
        seed = int(hashlib.sha256(seed_text.encode()).hexdigest()[:16], 16)
        rng = random.Random(seed)

        doy = day.timetuple().tm_yday
        seasonal = math.sin(2 * math.pi * (doy - 80) / 365.25)
        low = 8 + 13 * seasonal + rng.uniform(-3, 3)
        high = low + 6 + rng.uniform(0, 5)
        low = max(self.temp_min, min(self.temp_max - 1, round(low)))
        high = max(low + 1, min(self.temp_max, round(high)))
        # 夏季硬下限：避免曲线/天气修正把夏天压到零下
        if day.month in self.summer_months:
            low = max(low, self.summer_min_temp)
            high = max(high, low + 6)
            high = min(high, self.temp_max)

        snow_ok = self._in_snow_season(day)
        allowed = ([w for w in WEATHERS if snow_ok or w not in SNOW_WEATHERS])
        prev = None
        if isinstance(prev_state, dict) and prev_state.get("date"):
            prev_plan = prev_state.get("plan") or {}
            if isinstance(prev_plan, dict):
                prev = prev_plan.get("20")

        plan = {}
        for h in self.update_hours:
            plan[str(h)] = self._choose_weather(rng, prev, allowed)
            prev = plan[str(h)]
        return plan, low, high

    def _choose_weather(self, rng: random.Random, prev: str, allowed: list) -> str:
        if prev in allowed and rng.random() < self.keep_same:
            return prev
        pool = [w for w in allowed if w != prev] or allowed
        weights = [WEATHER_WEIGHTS.get(w, 1) for w in pool]
        return rng.choices(pool, weights=weights, k=1)[0]

    def _slot(self, hour: int) -> int:
        # 05–11 用 05 的天气，12–19 用 12，20–次日 04 用 20
        if 5 <= hour < 12:
            return 5
        if 12 <= hour < 20:
            return 12
        return 20

    def _temperature(self, now: datetime, low: int, high: int, weather: str) -> int:
        h = now.hour + now.minute / 60 + now.second / 3600
        if 5 <= h <= 14:
            frac = (h - 5) / 9.0
            t = low + (high - low) * math.sin(math.pi / 2 * frac)
        else:
            hh = h if h > 14 else h + 24
            frac = (hh - 14) / 15.0
            t = low + (high - low) * math.cos(math.pi / 2 * frac)
        offset = {
            "晴": 2, "多云": 1, "阴": 0,
            "小雨": -1, "中雨": -2, "大雨": -3,
            "雨夹雪": -4, "小雪": -4, "大雪": -6,
        }.get(weather, 0)
        return int(round(max(self.temp_min, min(self.temp_max, t + offset))))
