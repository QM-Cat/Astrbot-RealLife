# 真人生活模拟（astrbot_plugin_human_life）

> 插件仓库：<https://github.com/QM-Cat/Astrbot-RealLife>  
> 适用 AstrBot `>=4.24.0,<5.0.0`

让 Bot 不再“永远在线、随叫随到”，而是像真人一样有自己的作息和日常：

- 早上 07:30 起床吃饭、13:00 午睡、下午上课或打游戏……具体行为由**提示词**控制。
- 每隔一段时间，结合当前时间、生活提示词、上一刻状态和今天已经做过的事，让模型推断“现在在做什么”。
- 睡前把当天的时间线写成**第一人称日记**，一周结束再写**周记**。
- 聊天时自动把当前生活状态注入模型上下文，被问到“你在干嘛”时能自然回答。
- 通过指令查看当前状态、历史日记和周记。

## 效果示例

```text
🐾 我现在的状态
🕗 2026-10-27（周二）20:04
📍 宿舍 · 打游戏
💭 心情：放松
📝 排位输了两把，正在犹豫要不要再来一局
💡 再输就真的去洗澡
（1 分钟前更新）
```

```text
📔 2026-10-27（周二）的日记
────────────────
今天早上差点没起来，闹钟响了三遍才爬下床。食堂的豆浆还是老味道……
```

## 目录结构

```text
astrbot_plugin_human_life/
├── __init__.py
├── main.py              # 插件入口、调度循环、指令、聊天注入
├── life_engine.py       # 生活状态机、日记/周记生成、持久化与格式化
├── config.py            # 配置模型与默认值
├── prompts.py           # 内置提示词（可直接阅读和修改）
├── _conf_schema.json    # AstrBot WebUI 配置项
├── metadata.yaml        # 插件元数据
├── requirements.txt
└── tests/
    └── test_life_engine.py
```

## 安装

### 通过 AstrBot WebUI 安装（推荐）

1. 打开 AstrBot WebUI 的「插件」页面，选择「安装插件」。
2. 填入仓库地址：`https://github.com/QM-Cat/Astrbot-RealLife`。
3. 安装完成后启用插件，打开配置按需调整作息、提示词和日记时间。
4. 在聊天里发送 `/状态` 测试。

AstrBot 会按 `metadata.yaml` 中的 `name: astrbot_plugin_human_life` 把插件安装到 `data/plugins/astrbot_plugin_human_life`。

### 手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/QM-Cat/Astrbot-RealLife.git astrbot_plugin_human_life
```

然后重启 AstrBot，或在 WebUI 插件页重载插件。

> 仓库名是 `Astrbot-RealLife`，但插件目录名需要是 `astrbot_plugin_human_life`（与 `metadata.yaml` 的 `name` 一致），否则相对导入可能找不到模块。
>
> 插件数据会写到 `data/plugin_data/astrbot_plugin_human_life/life_data.json`，与插件代码分开；更新/重装插件不会覆盖数据。

## 工作方式

### 1. 定时推断当前活动

后台每 30 秒检查一次，到了 `update_interval_minutes`（默认 25 分钟）就调用一次模型：

```text
当前时间 + 星期 + 距离上次记录多久
+ 上一刻状态
+ 今天的时间线
+ 生活设定提示词
        ↓
模型输出 JSON：activity / location / mood / detail / thought
        ↓
写入 current + timeline
```

模型输出不是 JSON 时，会退化成“第一行当活动、全文当细节”，不会因为一次格式错误停摆。

### 2. 睡眠时段

`active_start` 到 `active_end`（默认 07:30–23:30）之外视为睡眠：

- 不调用模型，节省费用；
- 状态显示为“睡觉”，聊天注入会提示“可能是被消息吵醒的”；
- 到了早上第一次更新时，会把“睡觉”作为上一刻状态，自然衔接到起床。

如果希望整夜也持续模拟，可以打开 `allow_night_update`。

### 3. 日记与周记

- 每天 `daily_time`（默认 23:30）后，取当天完整时间线生成日记。放在 18:00–23:59 时写当天；放在 00:00–11:59 时写前一天，适合熬到零点再写日记的作息。
- 每周 `weekly_day` + `weekly_time`（默认周日 23:50）后，汇总本周日记生成周记。
- 如果周记时间早于日记时间，周记可能不包含当天的日记；默认配置已经错开。
- Bot 重启后会补写“昨天/今天”尚未写的日记（最多两次），不会无限追历史。
- 生成失败会按 15/30 分钟退避重试，避免频繁打模型。

### 4. 聊天状态注入

通过 AstrBot 的 `on_llm_request` 钩子，把下面这样的块追加到 `system_prompt`：

```text
<!-- human_life_status:start -->
【你此刻的生活状态】
现在是 2026-10-27（周二）20:04。
你现在在宿舍打游戏。
心情：放松
细节：排位输了两把，正在犹豫要不要再来一局
脑子里：再输就真的去洗澡
这是你真实的生活，不是用户告诉你的，也不是临时编的。聊天时可以自然带入……
<!-- human_life_status:end -->
```

- 用注释标记包裹，重复触发不会重复注入。
- 状态超过 `status_max_age_hours` 未更新时不再注入，避免把陈旧状态带进聊天。
- 可以在配置里关闭 `inject_status`。

## 指令

| 指令 | 说明 |
| --- | --- |
| `/状态` | 查看 Bot 此刻在做什么 |
| `/日记` | 查看最新一篇日记 |
| `/日记 今天` / `/日记 昨天` | 查看指定日期 |
| `/日记 2026-10-27` | 查看指定日期（也支持 `2026/10/27`、`10-27`） |
| `/日记 列表` 或 `/日记列表` | 列出最近 10 篇 |
| `/周记` | 查看最新一篇周记 |
| `/周记 本周` / `/周记 上周` | 查看本周/上周 |
| `/周记 2026-W44` | 查看指定 ISO 周 |
| `/周记 列表` 或 `/周记列表` | 列出最近 8 篇 |
| `/生活帮助` | 显示用法 |
| `/生活刷新` | 管理员：立即重新推断一次状态 |
| `/生活会话` | 管理员：显示当前会话 UMO，方便配置自动发送 |

> 指令前缀取决于 AstrBot 的唤醒/指令前缀配置；上面用 `/` 示意。

## 配置说明

WebUI 中分为五组：

### 基础设置

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关；关闭后不再定时更新 |
| `timezone` | `Asia/Shanghai` | 计算作息使用的时区 |
| `provider_id` | 空 | 留空用当前对话模型，也可指定 Provider ID |
| `inject_status` | `true` | 聊天时注入当前状态 |

### 作息与状态更新

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `update_interval_minutes` | `25` | 状态更新间隔（分钟） |
| `active_start` | `07:30` | 白天开始；之外视为睡眠 |
| `active_end` | `23:30` | 白天结束 |
| `allow_night_update` | `false` | 夜间也调用模型 |
| `sleep_activity` | `睡觉` | 睡眠状态名 |
| `sleep_location` | `宿舍` | 睡眠地点 |
| `sleep_detail` | 已睡着…… | 睡眠细节 |

### 日记与周记

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `daily_time` | `23:30` | 每日日记时间 |
| `weekly_day` | `7` | 周记日，1=周一，7=周日 |
| `weekly_time` | `23:50` | 周记时间 |
| `notify_session` | 空 | 生成后发送到哪个会话（UMO） |
| `notify_daily` | `false` | 自动发送日记 |
| `notify_weekly` | `false` | 自动发送周记 |
| `min_activities_for_diary` | `3` | 当天活动太少时不硬写 |

获取 UMO：在目标会话发送 `/生活会话`，把返回的整串填进 `notify_session`。

### 提示词

**行为完全由提示词控制。** 五个字段留空时使用 `prompts.py` 中的内置默认值；想改作息、性格、写作风格，直接改这里即可（也可以改 `prompts.py` 后重载插件）。

| 字段 | 作用 | 可用变量 |
| --- | --- | --- |
| `life_prompt` | 生活设定：身份、作息、行为规则 | 无 |
| `activity_prompt` | 推断此刻在做什么 | `{date}` `{weekday}` `{time}` `{elapsed}` `{previous}` `{timeline}` |
| `daily_prompt` | 写日记 | `{date}` `{weekday}` `{timeline}` `{activity_count}` |
| `weekly_prompt` | 写周记 | `{week_start}` `{week_end}` `{week_label}` `{source_note}` `{material}` `{diary_count}` `{day_count}` |
| `injection_template` | 聊天状态注入模板 | `{date}` `{weekday}` `{time}` `{activity}` `{location}` `{mood}` `{activity_line}` `{mood_line}` `{detail_line}` `{thought_line}` `{sleep_hint}` |

变量使用单次正则替换，提示词里的 JSON 花括号不会被误伤；未知变量会原样保留。

### 高级设置

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `request_timeout_seconds` | `120` | 单次模型请求超时 |
| `keep_days` | `30` | 时间线保留天数（日记保留 365 天） |
| `max_timeline_items` | `16` | 推断时参考的最近活动数 |
| `max_timeline_per_day` | `80` | 每天最多保存多少条 |
| `status_max_age_hours` | `12` | 状态注入最大时效 |

## 自定义提示词示例

比如把 Bot 改成“上班族”：

```text
你正在经营一个真实的人的日常生活。

【生活设定】
- 你是一名刚工作两年的程序员，租住在公司附近。
- 工作日 08:00 起床，09:30 到公司，18:30 下班。
- 中午 12:00 吃饭，13:00 趴在工位上午睡。
- 晚上回家做饭、打游戏、看番，00:30 前睡觉。
- 周末会睡懒觉、出门买东西，或者在家写 Side Project。

【行为规则】
1. 活动要具体，符合时间和地点。
2. 允许疲惫、拖延、摸鱼，不要一直积极向上。
3. 不要提到“模拟”“提示词”“AI”。
```

然后相应调整 `active_start=08:00`、`active_end=00:30`、`daily_time=00:15`。  
`daily_time` 在 18:00–23:59 写当天日记，在 00:00–11:59 写前一天日记；所以 00:15 正好对应“睡前写今天（刚结束的这一天）”。

## 数据与备份

- 数据文件：`data/plugin_data/astrbot_plugin_human_life/life_data.json`
- 结构：`current`、`timeline`、`diaries`、`weeklies`
- 写入方式：临时文件 + `os.replace` 原子替换
- 文件损坏时会自动改名为 `life_data.corrupt-<时间>.json`，然后从空数据重新开始
- 卸载插件不会删除数据；需要清空时手动删除该文件

## 兼容性与注意事项

- 需要 AstrBot `>= 4.24.0, < 5.0.0`（在 4.29.3 上验证）。
- 只依赖标准库和 AstrBot 自带 API，无第三方 Python 依赖。
- 定时任务使用 `asyncio`，插件卸载/重载时会取消，不会残留后台任务。
- 每次状态更新、日记、周记都会调用一次模型；默认一天约 40 次状态更新 + 1 篇日记，按需调整间隔。
- 模型最好支持稳定的中文 JSON 输出；即使不支持，也有纯文本兜底。
- 日记默认所有人可查看；如果需要权限控制，可以在 `main.py` 的指令上追加 `@filter.permission_type(...)`。

## 测试

不需要启动 AstrBot：

```bash
cd astrbot_plugin_human_life
python -m unittest discover -s tests -v
```

测试覆盖：状态更新与间隔、夜间睡眠、日记只写一次、周记与跨周、JSON/纯文本解析、日期与周参数、模板渲染。

## 常见问题

**Q：为什么状态一直是“睡觉”？**  
A：检查当前时间是否在 `active_start`–`active_end` 之外，以及时区 `timezone` 是否配置正确。

**Q：日记没有生成？**  
A：当天活动数需达到 `min_activities_for_diary`；并且要过了 `daily_time`。刚安装时没有历史数据，属于正常现象。

**Q：周记没有生成？**  
A：周记需要本周至少有一篇日记，或至少两天的活动记录；默认在周日 23:50 之后生成。

**Q：聊天里没有状态？**  
A：确认 `inject_status=true`，并且当前状态没有超过 `status_max_age_hours`；第一次启动需要等后台完成一次更新。

**Q：能同时开多个 Bot 人格吗？**  
A：插件数据按插件实例共享，不建议同一 AstrBot 实例里跑多份不同人格的副本；需要隔离时可以复制插件目录并修改 `PLUGIN_NAME`/数据文件名。

## License

本项目使用 [GNU Affero General Public License v3.0](LICENSE) 许可协议。

