"""插件配置模型。

AstrBot 的配置是嵌套 dict；这里把它收敛成一个带默认值的 dataclass，
让引擎和命令层只关心业务字段。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .prompts import (
    DEFAULT_ACTIVITY_PROMPT,
    DEFAULT_DAILY_PROMPT,
    DEFAULT_INJECTION_TEMPLATE,
    DEFAULT_LIFE_PROMPT,
    DEFAULT_WEEKLY_PROMPT,
    pick_prompt,
)

PLUGIN_NAME = "astrbot_plugin_human_life"


def _section(config: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = config.get(name)
    return value if isinstance(value, Mapping) else {}


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on", "是", "开"}:
            return True
        if lowered in {"false", "0", "no", "off", "否", "关"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_int(
    value: Any,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _as_str(value: Any, default: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default


def parse_hhmm(value: str, default: str) -> tuple[int, int]:
    """解析 ``HH:MM``；失败时回退到默认值。"""
    for candidate in (value, default):
        try:
            hour_str, minute_str = str(candidate).strip().split(":", 1)
            hour, minute = int(hour_str), int(minute_str)
        except (ValueError, AttributeError):
            continue
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    return 0, 0


def format_hhmm(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


@dataclass(slots=True)
class LifeSettings:
    """运行时配置。"""

    enabled: bool = True
    timezone: str = "Asia/Shanghai"
    provider_id: str = ""
    inject_status: bool = True

    update_interval_minutes: int = 25
    active_start: str = "07:30"
    active_end: str = "23:30"
    allow_night_update: bool = False
    sleep_activity: str = "睡觉"
    sleep_location: str = "宿舍"
    sleep_detail: str = "已经睡着了，呼吸很轻，偶尔翻个身。"

    daily_time: str = "23:30"
    weekly_day: int = 7
    weekly_time: str = "23:50"
    notify_session: str = ""
    notify_daily: bool = False
    notify_weekly: bool = False
    min_activities_for_diary: int = 3

    request_timeout_seconds: int = 120
    keep_days: int = 30
    max_timeline_items: int = 16
    max_timeline_per_day: int = 80
    status_max_age_hours: int = 12

    life_prompt: str = DEFAULT_LIFE_PROMPT
    activity_prompt: str = DEFAULT_ACTIVITY_PROMPT
    daily_prompt: str = DEFAULT_DAILY_PROMPT
    weekly_prompt: str = DEFAULT_WEEKLY_PROMPT
    injection_template: str = DEFAULT_INJECTION_TEMPLATE

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any] | None) -> "LifeSettings":
        config = config or {}
        base = _section(config, "base")
        schedule = _section(config, "schedule")
        diary = _section(config, "diary")
        prompts = _section(config, "prompts")
        advanced = _section(config, "advanced")

        # 兼容早期/手写的扁平配置：优先读分组，缺失时回退到顶层。
        def get(group: Mapping[str, Any], key: str, default: Any) -> Any:
            if key in group:
                return group[key]
            return config.get(key, default)

        weekly_day = _as_int(
            get(diary, "weekly_day", 7), 7, minimum=1, maximum=7
        )
        return cls(
            enabled=_as_bool(get(base, "enabled", True), True),
            timezone=_as_str(get(base, "timezone", "Asia/Shanghai"), "Asia/Shanghai"),
            provider_id=_as_str(get(base, "provider_id", ""), ""),
            inject_status=_as_bool(get(base, "inject_status", True), True),
            update_interval_minutes=_as_int(
                get(schedule, "update_interval_minutes", 25),
                25,
                minimum=1,
                maximum=720,
            ),
            active_start=_as_str(
                get(schedule, "active_start", "07:30"), "07:30"
            ),
            active_end=_as_str(get(schedule, "active_end", "23:30"), "23:30"),
            allow_night_update=_as_bool(
                get(schedule, "allow_night_update", False), False
            ),
            sleep_activity=_as_str(
                get(schedule, "sleep_activity", "睡觉"), "睡觉"
            ),
            sleep_location=_as_str(
                get(schedule, "sleep_location", "宿舍"), "宿舍"
            ),
            sleep_detail=_as_str(
                get(schedule, "sleep_detail", "已经睡着了，呼吸很轻，偶尔翻个身。"),
                "已经睡着了，呼吸很轻，偶尔翻个身。",
            ),
            daily_time=_as_str(get(diary, "daily_time", "23:30"), "23:30"),
            weekly_day=weekly_day,
            weekly_time=_as_str(get(diary, "weekly_time", "23:50"), "23:50"),
            notify_session=_as_str(get(diary, "notify_session", ""), ""),
            notify_daily=_as_bool(get(diary, "notify_daily", False), False),
            notify_weekly=_as_bool(get(diary, "notify_weekly", False), False),
            min_activities_for_diary=_as_int(
                get(diary, "min_activities_for_diary", 3),
                3,
                minimum=1,
                maximum=20,
            ),
            request_timeout_seconds=_as_int(
                get(advanced, "request_timeout_seconds", 120),
                120,
                minimum=10,
                maximum=900,
            ),
            keep_days=_as_int(
                get(advanced, "keep_days", 30), 30, minimum=3, maximum=3650
            ),
            max_timeline_items=_as_int(
                get(advanced, "max_timeline_items", 16),
                16,
                minimum=3,
                maximum=100,
            ),
            max_timeline_per_day=_as_int(
                get(advanced, "max_timeline_per_day", 80),
                80,
                minimum=10,
                maximum=500,
            ),
            status_max_age_hours=_as_int(
                get(advanced, "status_max_age_hours", 12),
                12,
                minimum=1,
                maximum=168,
            ),
            life_prompt=pick_prompt(
                get(prompts, "life_prompt", ""), DEFAULT_LIFE_PROMPT
            ),
            activity_prompt=pick_prompt(
                get(prompts, "activity_prompt", ""), DEFAULT_ACTIVITY_PROMPT
            ),
            daily_prompt=pick_prompt(
                get(prompts, "daily_prompt", ""), DEFAULT_DAILY_PROMPT
            ),
            weekly_prompt=pick_prompt(
                get(prompts, "weekly_prompt", ""), DEFAULT_WEEKLY_PROMPT
            ),
            injection_template=pick_prompt(
                get(prompts, "injection_template", ""),
                DEFAULT_INJECTION_TEMPLATE,
            ),
        )

    @property
    def tzinfo(self) -> tzinfo:
        """返回配置的时区；无效时回退到系统本地时区。"""
        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            local = datetime.now().astimezone().tzinfo
            return local or ZoneInfo("UTC")

    @property
    def active_start_time(self) -> tuple[int, int]:
        return parse_hhmm(self.active_start, "07:30")

    @property
    def active_end_time(self) -> tuple[int, int]:
        return parse_hhmm(self.active_end, "23:30")

    @property
    def daily_clock(self) -> tuple[int, int]:
        return parse_hhmm(self.daily_time, "23:30")

    @property
    def weekly_clock(self) -> tuple[int, int]:
        return parse_hhmm(self.weekly_time, "23:50")

    @property
    def update_interval(self) -> timedelta:
        return timedelta(minutes=self.update_interval_minutes)

    def describe(self) -> str:
        """给日志用的一行摘要，不包含提示词正文。"""
        start = format_hhmm(*self.active_start_time)
        end = format_hhmm(*self.active_end_time)
        daily = format_hhmm(*self.daily_clock)
        weekly = format_hhmm(*self.weekly_clock)
        return (
            f"enabled={self.enabled} interval={self.update_interval_minutes}min "
            f"active={start}-{end} daily={daily} weekly={self.weekly_day}/{weekly} "
            f"inject={self.inject_status} tz={self.timezone}"
        )
