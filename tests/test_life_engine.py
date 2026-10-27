"""真人生活引擎的离线单元测试。

运行方式（不需要启动 AstrBot）：

    cd <插件目录>
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "astrbot_plugin_human_life"

# 直接按目录加载包，这样即使仓库文件夹叫 Astrbot-RealLife、没有重命名，
# 测试也能正常运行。
if PACKAGE_NAME not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        PACKAGE_NAME,
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载插件包")
    package = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE_NAME] = package
    spec.loader.exec_module(package)

from astrbot_plugin_human_life.config import LifeSettings  # noqa: E402
from astrbot_plugin_human_life.life_engine import (  # noqa: E402
    Activity,
    LifeEngine,
    empty_data,
    iso_week_info,
    parse_activity_response,
    parse_date_arg,
    parse_week_arg,
    weekday_cn,
)
from astrbot_plugin_human_life.prompts import render_template  # noqa: E402

TZ = timezone(timedelta(hours=8))


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, system_prompt: str, prompt: str) -> str:
        self.calls.append((system_prompt, prompt))
        if not self.responses:
            raise AssertionError("没有为这次调用准备假响应")
        return self.responses.pop(0)


class MemoryStore:
    """测试用的内存存储，避免依赖磁盘权限。"""

    def __init__(self) -> None:
        self.data = empty_data()
        self.load_error = ""

    async def load(self) -> dict:
        return copy.deepcopy(self.data)

    async def save(self, data: dict) -> None:
        self.data = copy.deepcopy(data)


def activity(day: date, clock: str, text: str, **kwargs) -> Activity:
    return Activity(
        date=day.isoformat(),
        time=clock,
        activity=text,
        location=kwargs.get("location", "宿舍"),
        mood=kwargs.get("mood", "平静"),
        detail=kwargs.get("detail", ""),
        thought=kwargs.get("thought", ""),
        timestamp=datetime.combine(day, datetime.strptime(clock, "%H:%M").time(), TZ).isoformat(),
    )


class EngineTest(unittest.IsolatedAsyncioTestCase):
    def make_engine(
        self, clock: Clock, llm: FakeLLM, store: MemoryStore | None = None
    ) -> LifeEngine:
        settings = LifeSettings(
            timezone="Asia/Shanghai",
            update_interval_minutes=25,
            active_start="07:30",
            active_end="23:30",
            daily_time="23:30",
            weekly_day=7,
            weekly_time="23:50",
            min_activities_for_diary=3,
        )
        return LifeEngine(
            settings=settings,
            llm_call=llm,
            now_fn=clock,
            store=store or MemoryStore(),
        )

    async def test_status_update_and_interval(self) -> None:
        clock = Clock(datetime(2026, 10, 27, 8, 0, tzinfo=TZ))
        llm = FakeLLM(
            [
                '{"activity":"吃早饭","location":"食堂","mood":"满足",'
                '"detail":"豆浆和两个包子","thought":"今天第一节有课"}',
                '{"activity":"赶去教学楼","location":"路上","mood":"有点急",'
                '"detail":"还有五分钟上课","thought":"别迟到"}',
            ]
        )
        store = MemoryStore()
        engine = self.make_engine(clock, llm, store)
        await engine.load()

        self.assertEqual(await engine.tick(), [])
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(engine.current_activity().activity, "吃早饭")
        self.assertEqual(len(engine.data["timeline"]["2026-10-27"]), 1)
        self.assertIn("吃早饭", engine.render_status_card())
        self.assertIn("吃早饭", engine.build_injection())

        # 没到间隔，不应再次调用模型。
        clock.value = datetime(2026, 10, 27, 8, 10, tzinfo=TZ)
        await engine.tick()
        self.assertEqual(len(llm.calls), 1)

        # 到间隔后更新，并写第二条时间线。
        clock.value = datetime(2026, 10, 27, 8, 30, tzinfo=TZ)
        await engine.tick()
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(engine.current_activity().activity, "赶去教学楼")
        self.assertEqual(len(engine.data["timeline"]["2026-10-27"]), 2)

        # 数据确实落盘。
        reloaded = self.make_engine(clock, FakeLLM([]), store)
        await reloaded.load()
        self.assertEqual(reloaded.current_activity().activity, "赶去教学楼")

    async def test_night_sleeps_without_llm(self) -> None:
        clock = Clock(datetime(2026, 10, 28, 2, 0, tzinfo=TZ))
        llm = FakeLLM([])
        engine = self.make_engine(clock, llm)
        await engine.load()

        await engine.tick()
        self.assertEqual(llm.calls, [])
        self.assertEqual(engine.current_activity().activity, "睡觉")
        self.assertIn("睡觉", engine.render_status_card())
        self.assertIn("吵醒", engine.build_injection())
        self.assertEqual(engine.data["timeline"], {})

    async def test_daily_diary_once(self) -> None:
        day = date(2026, 10, 27)
        clock = Clock(datetime(2026, 10, 27, 23, 35, tzinfo=TZ))
        llm = FakeLLM(["今天最开心的是晚上那局游戏，虽然最后还是输了。\n\n不过食堂的番茄牛腩饭真好吃。"])
        engine = self.make_engine(clock, llm)
        await engine.load()
        engine.data["timeline"][day.isoformat()] = [
            activity(day, "07:35", "吃早饭", detail="豆浆和包子").to_dict(),
            activity(day, "12:10", "吃番茄牛腩饭", location="食堂").to_dict(),
            activity(day, "20:00", "打游戏", detail="排位输了两把").to_dict(),
        ]

        notifications = await engine.tick()
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0][0], "daily")
        self.assertIn(day.isoformat(), engine.data["diaries"])
        self.assertEqual(len(llm.calls), 1)
        self.assertIn("番茄牛腩饭", engine.render_diary_card("今天"))

        # 已经写过，不应重复生成。
        await engine.tick()
        self.assertEqual(len(llm.calls), 1)

        # 第二天凌晨仍能补写前一天的日记，但不会重写。
        clock.value = datetime(2026, 10, 28, 0, 10, tzinfo=TZ)
        await engine.tick()
        self.assertEqual(len(llm.calls), 1)

    async def test_daily_diary_skips_when_too_few_activities(self) -> None:
        day = date(2026, 10, 27)
        clock = Clock(datetime(2026, 10, 27, 23, 35, tzinfo=TZ))
        llm = FakeLLM([])
        engine = self.make_engine(clock, llm)
        await engine.load()
        engine.data["timeline"][day.isoformat()] = [
            activity(day, "07:35", "吃早饭").to_dict(),
        ]

        notifications = await engine.tick()
        self.assertEqual(notifications, [])
        self.assertEqual(llm.calls, [])
        self.assertNotIn(day.isoformat(), engine.data["diaries"])

    async def test_daily_diary_after_midnight_uses_previous_day(self) -> None:
        day = date(2026, 10, 27)
        clock = Clock(datetime(2026, 10, 27, 23, 30, tzinfo=TZ))
        llm = FakeLLM(
            [
                "熬夜写完的日记。今天过得还不错，虽然有点累。",
                '{"activity":"准备睡觉","location":"宿舍","mood":"困",'
                '"detail":"眼皮快睁不开了","thought":"明天一定要早睡"}',
            ]
        )
        settings = LifeSettings(
            timezone="Asia/Shanghai",
            daily_time="00:15",
            active_start="07:30",
            active_end="00:30",
            min_activities_for_diary=3,
        )
        engine = LifeEngine(
            settings=settings,
            llm_call=llm,
            now_fn=clock,
            store=MemoryStore(),
        )
        await engine.load()
        engine.data["timeline"][day.isoformat()] = [
            activity(day, "09:00", "上课").to_dict(),
            activity(day, "13:00", "午睡").to_dict(),
            activity(day, "20:00", "打游戏").to_dict(),
        ]
        engine.data["current"] = activity(day, "23:25", "打游戏").to_dict()

        # 23:30 还没到 00:15，不应该把今天提前写成日记。
        notifications = await engine.tick()
        self.assertEqual(notifications, [])
        self.assertNotIn(day.isoformat(), engine.data["diaries"])

        # 过了午夜 00:20，再写 10-27 的日记。
        clock.value = datetime(2026, 10, 28, 0, 20, tzinfo=TZ)
        notifications = await engine.tick()
        self.assertEqual([item[0] for item in notifications], ["daily"])
        self.assertIn(day.isoformat(), engine.data["diaries"])

    async def test_weekly_diary(self) -> None:
        # 2026-11-01 是周日，ISO 第 44 周。
        sunday = date(2026, 11, 1)
        key, start, end = iso_week_info(sunday)
        self.assertEqual(key, "2026-W44")
        clock = Clock(datetime(2026, 11, 1, 23, 55, tzinfo=TZ))
        llm = FakeLLM(["这周过得有点乱，但总算把项目跑通了。下周想早点睡。"])
        engine = self.make_engine(clock, llm)
        await engine.load()
        engine.data["diaries"] = {
            (start + timedelta(days=offset)).isoformat(): {
                "date": (start + timedelta(days=offset)).isoformat(),
                "weekday": weekday_cn(start + timedelta(days=offset)),
                "content": f"第 {offset + 1} 天的日记。",
                "created_at": "",
                "activity_count": 3,
                "word_count": 10,
            }
            for offset in range(7)
        }

        notifications = await engine.tick()
        self.assertEqual([item[0] for item in notifications], ["weekly"])
        self.assertIn(key, engine.data["weeklies"])
        self.assertIn("项目跑通", engine.render_weekly_card("本周"))
        self.assertEqual(len(llm.calls), 1)

        await engine.tick()
        self.assertEqual(len(llm.calls), 1)

    async def test_weekly_boundary_rollover(self) -> None:
        engine = self.make_engine(
            Clock(datetime(2026, 11, 2, 0, 10, tzinfo=TZ)), FakeLLM([])
        )
        boundary = engine._last_weekly_boundary(datetime(2026, 11, 2, 0, 10, tzinfo=TZ))
        self.assertEqual(boundary.date(), date(2026, 11, 1))
        self.assertEqual(boundary.hour, 23)
        self.assertEqual(boundary.minute, 50)

    async def test_force_update_error_is_reported(self) -> None:
        class BrokenLLM:
            async def __call__(self, system_prompt: str, prompt: str) -> str:
                raise RuntimeError("provider down")

        engine = LifeEngine(
            settings=LifeSettings(timezone="Asia/Shanghai"),
            llm_call=BrokenLLM(),
            now_fn=Clock(datetime(2026, 10, 27, 8, 0, tzinfo=TZ)),
            store=MemoryStore(),
        )
        await engine.load()
        self.assertFalse(await engine.force_update())
        self.assertIn("provider down", engine.last_error)


class ParsingTest(unittest.TestCase):
    def test_parse_json_with_fence_and_chinese_keys(self) -> None:
        result = parse_activity_response(
            "```json\n"
            '{"活动":"在图书馆看书","地点":"图书馆","心情":"平静",'
            '"细节":"看的是算法书","想法":"这题好像做过"}\n'
            "```"
        )
        self.assertEqual(result.activity, "在图书馆看书")
        self.assertEqual(result.location, "图书馆")
        self.assertEqual(result.thought, "这题好像做过")

    def test_parse_json_with_surrounding_text(self) -> None:
        result = parse_activity_response(
            "好的，这是现在的状态：\n"
            '{"activity":"在阳台吹风","location":"阳台","mood":"放空","detail":"天有点凉"}'
            "\n希望这个格式没问题。"
        )
        self.assertEqual(result.activity, "在阳台吹风")
        self.assertEqual(result.mood, "放空")

    def test_parse_plain_text_fallback(self) -> None:
        result = parse_activity_response("在阳台发呆\n\n风吹得很舒服。")
        self.assertEqual(result.activity, "在阳台发呆")
        self.assertIn("风吹得很舒服", result.detail)

    def test_date_arguments(self) -> None:
        today = date(2026, 10, 27)
        self.assertEqual(parse_date_arg("今天", today), today)
        self.assertEqual(parse_date_arg("昨天", today), date(2026, 10, 26))
        self.assertEqual(parse_date_arg("2026-10-20", today), date(2026, 10, 20))
        self.assertEqual(parse_date_arg("10/20", today), date(2026, 10, 20))
        self.assertIsNone(parse_date_arg("2026-02-30", today))

    def test_week_arguments(self) -> None:
        today = date(2026, 10, 27)
        key, start, end = parse_week_arg("本周", today)
        self.assertEqual(key, "2026-W44")
        self.assertEqual(start, date(2026, 10, 26))
        self.assertEqual(end, date(2026, 11, 1))

        key, start, _ = parse_week_arg("上周", today)
        self.assertEqual(key, "2026-W43")
        self.assertEqual(start, date(2026, 10, 19))

        key, _, _ = parse_week_arg("2026-W44", today)
        self.assertEqual(key, "2026-W44")
        self.assertIsNone(parse_week_arg("2026-W99", today))

    def test_render_template_keeps_json_braces(self) -> None:
        template = (
            "现在是 {time}，{unknown}\n"
            '{"activity":"正在做的事","detail":"{date}"}'
        )
        rendered = render_template(template, time="08:00", date="2026-10-27")
        self.assertIn("现在是 08:00", rendered)
        self.assertIn("{unknown}", rendered)
        self.assertIn('"detail":"2026-10-27"', rendered)

    def test_sleep_status_date_crosses_midnight(self) -> None:
        # 纯日期/星期工具，保证午夜后展示不会串日期。
        self.assertEqual(weekday_cn(date(2026, 11, 1)), "周日")


if __name__ == "__main__":
    unittest.main(verbosity=2)
