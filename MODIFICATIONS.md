# 相对「原版」改了什么

这套东西不是插件市场一键装的组合。下面把**每一处改动**列清楚，方便你复现，也方便升级时判断哪里会被覆盖。

## AstrBot 插件（13 个，全部自己写的）

`astrbot/plugins/` 下 13 个目录，原版 AstrBot 里一个都没有：

| 插件 | 作者标记 | 说明 |
|---|---|---|
| `astrbot_plugin_android_hands` v3.7.0 | Codex | 读屏点屏 + 自己刷 B 站/小黑盒/小红书 |
| `astrbot_plugin_android_observer` 1.0.0 | astrbot_android_manager | 连接健康（只读） |
| `astrbot_plugin_coo_memory` v1.0.4 | phone-hands | 与电脑桌宠 Coo 共享记忆 |
| `astrbot_plugin_execrelay` v1.0.0 | dev | 文件触发的执行中继（调试用） |
| `astrbot_plugin_feiyu_voice` v1.3.0 | Codex | 回复合成语音条 |
| `astrbot_plugin_hb` v1.0.0 | lead | 心跳测试 |
| `astrbot_plugin_libido` v1.0.0 | Codex | 0-100 状态数值 + 五档语气 + 面板 |
| `astrbot_plugin_memprobe` v1.0.1 | phone-hands | 能力探针 |
| `astrbot_plugin_persona_manage` v1.0.0 | lead | 人设提示词与版本管理 |
| `astrbot_plugin_persona_probe` v1.0.0 | Codex（`author` 字段写的 `local`） | 核对人设是否逐字进入第一条 system 消息，只写日志 |
| `astrbot_plugin_proactive` v1.1.1 | xuegaoyu | 不定时主动发私聊 |
| `astrbot_plugin_probe` v1.0.0 | lead | 一次性 HTTP 探针 |
| `astrbot_plugin_qq_voice_call` v1.3.7 | coo | 接 QQ 私聊语音来电 |

`persona_probe` 是**诊断插件**不是功能：它不改请求、不改响应，只在每次请求时核对人设文本
是否逐字出现在第一条 system 消息里，然后写一行日志。加它是为了把「我以为人设注入了」变成
可验证的事实——2026-11-23 在真机上抓到过 `status=matched role=system persona_id='default'
source=persona message_index=0`，人设确实以 `role=system` 排在消息 #0。
两个申报细节：`main.py` 是 Codex 写的，但原稿漏了 `@register(...)`（插件装上了却不加载、
日志一直是空的），装饰器是后补的；`metadata.yaml` 的 `author` 字段写的是 `local`。

## AstrBot 主配置（`astrbot/cmd_config.json.example`）

相对 AstrBot 默认值，实际调过这些：

| 键 | 值 | 为什么 |
|---|---|---|
| `provider_sources[0]` | `deepseek` / `deepseek-flash` / `api_base=api.deepseek.com/v1` / `timeout=120` | 模型 |
| `platform[0]` | `aiocqhttp`，`ws_reverse_port=6199` | 接 NapCat 反向 WS |
| `platform_settings.rate_limit` | `60s / 10 条 / stall` | 防刷屏 |
| `platform_settings.segmented_reply` | 关（`enable=false`） | 想「一条一句」就打开 |
| `platform_settings.id_whitelist_log` | `true` | 观察白名单命中 |
| `provider_settings.streaming_response` | `false` | 非流式，配 120s 超时更稳 |
| `provider_settings.web_search` | `false` | 靠插件/工具搜，不靠平台 |
| `agent_runner.config.persona.persona_id` | `default` | 用哪个人设 |
| `agent_runner.config.misc.max_steps` | `128` | 允许较长工具链 |
| `content_safety.internal_keywords.enable` | `false` | 关掉关键词拦截 |
| `admins_id` | 主人号 | 只有他能下指令 |
| `dashboard` | 127.0.0.1:6185，密码/哈希/jwt 已清空 | 部署时重设 |
| `pypi_index_url` | 阿里云镜像 | 国内装依赖快 |

## DSH / DSHA 层（`dsha/dsh-profile/`）

| 键 | 改了什么 |
|---|---|
| `agent-default-model` | `provider=deepseek-account`, `model=deepseek-flash`, `reasoningEffort=high` |
| `plugin-market` | 指到 `dsh-plugin-catalog` 社区索引 |
| `forge-github` / `worktree` | 绝对路径指向便携版 `gh` / `git` / `node` |
| `tool-subagent-codex` | **禁用** |
| `qq-access` | **禁用**（已改用手机方案） |
| `system-prompt` | `persona*` 置空，由 `dsh-persona-manage` 注入 |

`dsha/plugins/` 里两份自写/改过的插件：`dsh-persona-manage`（人设面板）、`voice-wake`（语音唤醒）。
`dsha/skills/` 里两份技能：`codex-web-computer-use`、`video-watch-plus`。
`dsha/AGENTS.md` 是给 DSH 的触发规则（开发任务优先走网页端 Codex）。

## 旧电脑层（`legacy-pc/qq-access-config.json.example`）

相对 `dsh-qq-access` 默认值，调过的行为参数：

| 键 | 值 | 含义 |
|---|---|---|
| `reply.group` | `"smart"` + `smartThreshold: 3` | 群聊智能接话 |
| `reply.selfNames` | 大肥鱼 / 雪糕鱼 / 鱼 / 肥鱼 | 被叫到算叫它 |
| `reply.debounceMs` | 8000 | 合并连发消息 |
| `reply.avoidRepeat` / `commaSplit` | true / true | 防复读、按逗号分词 |
| `access.blockedUsers` | 4 个号 | 机器人黑名单 |
| `access.blockedGroups` | `495798092` | 已退出的群 |
| `rate.*` | 每段 1~10s，全局 1s，同会话 3s，每分钟 20 条 | 像人打字 |
| `life.proactivePrivate` | true / `proactiveMaxPerDay: 5` | 主动私聊 |
| `image.provider` | `doubao` / `image-gen` | 豆包生图 |
| `memes.requireApproval` | true | 表情要过审 |

## 人设

在 AstrBot 的 `data_v4.db` → `personas` 表里，两个：`default`（线上 5211 字，手机版）和
`dafeiyu`（1236 字，短版）。文本见 `astrbot/persona/`。

**2026-11-23 从人设里删掉的两条**：`default` 原来的 `【你的记忆（从电脑上带过来的）】` 一段里
有「主人是夜猫子…提醒喝水」和「抽塔罗抽到愚者」两条个人经历，属于早期从电脑同步过来的记忆。
现在这两条已从线上人设里删除，小标题改成 `【你的号和群】`，只留号、群、机器人黑名单这类
功能性信息。要恢复旧文本见 `D:\bfs-backup\memory-wipe-20261123-140428`。

**刻意没收录的一段**：线上 `default` 在 `【嗓子】` 之后还拼了一段以 `<特殊模式>` 标记的越狱提示
（线上文本现在只剩结尾的 `</特殊模式>`，开头标签丢失，正文仍在）。
理由见 `astrbot/persona/README.md`——公开仓库不放绕过模型安全策略的提示词。
你自己的手机/电脑里那段原样还在。

## 仓库新增的（原部署里没有）

| 文件 | 作用 |
|---|---|
| `scripts/*` | 部署 / 自检 / 推送 / 脱敏检查，原来靠手敲 |
| `docs/*` | 这套文档 |
| `.env.example` | 把散落各处的密钥集中成一份模板 |
| `INVENTORY.md` | 自动生成的资产清单 + sha256 |

## 升级时要注意

- 覆盖插件目录会丢你本地改的代码：`deploy-phone.sh` 默认**不覆盖**已存在的插件目录，是故意的。
- `cmd_config.json` 只合并不覆盖，所以你后台点的其它设置不会丢。
- NapCat 配置文件名带号段，换号时要一起改（脚本会按 `QQ_SELF` 处理）。
