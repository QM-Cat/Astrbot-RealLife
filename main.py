"""AstrBot 真人生活模拟插件入口。

核心能力：
* 按配置的时间间隔，用提示词让模型推断 Bot 此刻在做什么；
* 睡前生成日记、周末生成周记；
* 通过 ``on_llm_request`` 把当前状态注入聊天上下文；
* 提供状态、日记、周记查询指令。
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from typing import Any

import astrbot.api.star as star
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.api.provider import ProviderRequest

from .config import PLUGIN_NAME, LifeSettings
from .life_engine import LifeEngine

TICK_SECONDS = 30
STATUS_MARKER = "<!-- human_life_status:start -->"


class HumanLifePlugin(star.Star):
    """让 Bot 拥有自己的作息、当前活动、日记和周记。

    定时根据当前时间、生活提示词和之前在做的事，推断 Bot 现在在做什么；
    睡前写日记，周末写周记；聊天时自动把当前状态注入上下文。
    """

    def __init__(self, context: star.Context, config: Any = None) -> None:
        super().__init__(context)
        self.config: Any = config if config is not None else {}
        self.settings = LifeSettings.from_mapping(dict(self.config))
        self.data_dir: Path = star.StarTools.get_data_dir(PLUGIN_NAME)
        self.data_file = self.data_dir / "life_data.json"

        self.engine = LifeEngine(
            settings=self.settings,
            data_path=self.data_file,
            llm_call=self._call_llm,
            logger=self.logger,
        )
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._notify_session_warned = False

    # -- 生命周期 ---------------------------------------------------------

    async def initialize(self) -> None:
        """加载持久化数据并启动调度循环。"""
        if self._task and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            with suppress(Exception):
                await self.engine.flush()

        self.settings = LifeSettings.from_mapping(dict(self.config))
        self.engine = LifeEngine(
            settings=self.settings,
            data_path=self.data_file,
            llm_call=self._call_llm,
            logger=self.logger,
        )
        await self.engine.load()

        self._stopping = False
        self._notify_session_warned = False
        self._task = asyncio.create_task(self._run_loop(), name=PLUGIN_NAME)
        self.logger.info("[真人生活] 已启动：%s", self.settings.describe())
        self.logger.info("[真人生活] 数据文件：%s", self.data_file)

    async def terminate(self) -> None:
        """插件卸载/重载时停止后台任务。"""
        self._stopping = True
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        with suppress(Exception):
            await self.engine.flush()
        self.logger.info("[真人生活] 后台任务已停止。")

    async def _run_loop(self) -> None:
        # AstrBot 会先加载插件、再初始化 Provider；这里等 Provider 就绪，
        # 避免插件刚启动时白白失败一次并进入很长的重试冷却。
        if not await self._wait_for_provider():
            return
        while not self._stopping:
            try:
                notifications = await self.engine.tick()
                for kind, entry in notifications:
                    await self._notify(kind, entry)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 后台循环必须自愈
                self.logger.exception("[真人生活] 调度循环出现异常，将在下一轮重试")

            try:
                await asyncio.sleep(TICK_SECONDS)
            except asyncio.CancelledError:
                raise

    async def _wait_for_provider(self) -> bool:
        warned = False
        while not self._stopping:
            try:
                await self._get_provider()
                if warned:
                    self.logger.info("[真人生活] Provider 已就绪，开始生活模拟。")
                return True
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - Provider 可能还没加载完
                if not warned:
                    warned = True
                    self.logger.warning(
                        "[真人生活] 暂时没有可用 Provider，稍后重试：%s", exc
                    )
                await asyncio.sleep(10)
        return False

    # -- LLM --------------------------------------------------------------

    async def _get_provider(self) -> Any:
        if self.settings.provider_id:
            getter = getattr(self.context, "get_provider_by_id", None)
            provider = (
                getter(self.settings.provider_id) if callable(getter) else None
            )
            if provider is None:
                raise RuntimeError(
                    f"找不到配置的 Provider：{self.settings.provider_id}"
                )
            if not hasattr(provider, "text_chat"):
                raise RuntimeError(
                    f"Provider {self.settings.provider_id} 不支持文本对话"
                )
            return provider

        getter = getattr(self.context, "get_using_provider_async", None)
        if callable(getter):
            try:
                provider = await getter()
            except Exception:  # noqa: BLE001 - 回退到同步旧接口
                provider = self.context.get_using_provider()
        else:
            provider = self.context.get_using_provider()
        if provider is None:
            raise RuntimeError("当前没有可用的对话模型 Provider，请先在 AstrBot 配置")
        return provider

    async def _call_llm(self, system_prompt: str, prompt: str) -> str:
        provider = await self._get_provider()
        try:
            response = await asyncio.wait_for(
                provider.text_chat(prompt=prompt, system_prompt=system_prompt),
                timeout=self.settings.request_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"模型请求超时（{self.settings.request_timeout_seconds} 秒）"
            ) from exc

        text = (getattr(response, "completion_text", "") or "").strip()
        if not text:
            raise RuntimeError("模型返回了空内容")
        return text

    # -- 通知 -------------------------------------------------------------

    async def _notify(self, kind: str, entry: dict[str, Any]) -> None:
        if kind == "daily" and not self.settings.notify_daily:
            return
        if kind == "weekly" and not self.settings.notify_weekly:
            return

        session = (self.settings.notify_session or "").strip()
        if not session:
            if not self._notify_session_warned:
                self._notify_session_warned = True
                self.logger.warning(
                    "[真人生活] 已开启日记通知，但 diary.notify_session 为空，跳过发送。"
                )
            return

        if kind == "daily":
            text = self.engine.render_diary_card(str(entry.get("date", "")))
        else:
            text = self.engine.render_weekly_card(str(entry.get("week", "")))
        if not text:
            return

        try:
            sent = await self.context.send_message(
                session, MessageChain([Plain(text=text)])
            )
            if sent is False:
                self.logger.warning(
                    "[真人生活] 通知会话 %s 没有匹配到平台，消息未送达", session
                )
            else:
                self.logger.info("[真人生活] 已把 %s 发送到 %s", kind, session)
        except Exception:  # noqa: BLE001
            self.logger.exception("[真人生活] 发送 %s 通知失败", kind)

    # -- 聊天上下文注入 ---------------------------------------------------

    @filter.on_llm_request()
    async def inject_life_status(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        """把 Bot 当前的生活状态追加到 system_prompt。"""
        try:
            block = self.engine.build_injection()
        except Exception:  # noqa: BLE001 - 注入失败不能影响正常聊天
            self.logger.exception("[真人生活] 生成状态注入失败")
            return
        if not block:
            return
        current_prompt = req.system_prompt or ""
        if STATUS_MARKER in current_prompt:
            return
        req.system_prompt = current_prompt.rstrip() + "\n\n" + block
        self.logger.debug("[真人生活] 已注入当前状态（%d 字）", len(block))

    # -- 指令 -------------------------------------------------------------

    @filter.command("状态", alias={"生活状态", "生活在干嘛"})
    async def cmd_status(self, event: AstrMessageEvent):
        """查看 Bot 此刻在做什么。"""
        yield event.plain_result(self.engine.render_status_card())

    @filter.command("日记", alias={"看日记", "生活日记"})
    async def cmd_diary(self, event: AstrMessageEvent):
        """查看日记：/日记 [今天|昨天|日期|列表]。"""
        yield event.plain_result(
            self.engine.render_diary_card(self._extract_arg(event))
        )

    @filter.command("日记列表")
    async def cmd_diary_list(self, event: AstrMessageEvent):
        """列出最近的日记。"""
        yield event.plain_result(self.engine.render_diary_card("列表"))

    @filter.command("周记", alias={"看周记", "生活周记"})
    async def cmd_weekly(self, event: AstrMessageEvent):
        """查看周记：/周记 [本周|上周|2026-W44|列表]。"""
        yield event.plain_result(
            self.engine.render_weekly_card(self._extract_arg(event))
        )

    @filter.command("周记列表")
    async def cmd_weekly_list(self, event: AstrMessageEvent):
        """列出最近的周记。"""
        yield event.plain_result(self.engine.render_weekly_card("列表"))

    @filter.command("生活帮助", alias={"真人生活"})
    async def cmd_help(self, event: AstrMessageEvent):
        """查看真人生活插件的用法。"""
        yield event.plain_result(self._help_text())

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("生活会话")
    async def cmd_session(self, event: AstrMessageEvent):
        """查看当前会话 UMO，方便配置日记通知。"""
        umo = getattr(event, "unified_msg_origin", "") or ""
        yield event.plain_result(
            "当前会话 UMO：\n"
            f"{umo}\n\n"
            "把这一串填到插件配置的「日记与周记 → 通知会话」里，"
            "日记生成后就会自动发到这里。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("生活刷新", alias={"刷新生活"})
    async def cmd_refresh(self, event: AstrMessageEvent):
        """立即重新推断一次当前状态（管理员）。"""
        ok = await self.engine.force_update()
        if not ok:
            reason = self.engine.last_error or "未知错误"
            yield event.plain_result(f"刷新失败了：{reason}")
            return
        yield event.plain_result(
            "已经重新看了一眼现在的生活。\n\n" + self.engine.render_status_card()
        )

    @staticmethod
    def _extract_arg(event: AstrMessageEvent) -> str:
        text = (getattr(event, "message_str", "") or "").strip()
        if not text:
            return ""
        parts = text.split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""

    def _help_text(self) -> str:
        settings = self.settings
        daily = settings.daily_time
        weekly = f"每周{['一', '二', '三', '四', '五', '六', '日'][settings.weekly_day - 1]} {settings.weekly_time}"
        return (
            "🐾 真人生活模拟\n"
            "我会按自己的作息生活，聊天时会自然带入当前状态。\n\n"
            "/状态　查看我此刻在做什么\n"
            "/日记　查看最新日记\n"
            "/日记 昨天｜2026-10-27｜列表\n"
            "/周记　查看最新周记\n"
            "/周记 本周｜上周｜2026-W44｜列表\n"
            "/生活帮助　显示这份说明\n\n"
            f"每天 {daily} 左右写日记，{weekly} 写周记。\n"
            "管理员可用：/生活刷新、/生活会话。"
        )


__all__ = ["HumanLifePlugin"]
