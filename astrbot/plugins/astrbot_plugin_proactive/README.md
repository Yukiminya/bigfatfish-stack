# 不定时主动发消息 v1.1.0

适用于 Android proot Ubuntu 上的 AstrBot 4.29.3 和 Python 3.12。默认向 NapCat 私聊 `napcat:FriendMessage:641961016` 发送主动消息，正文使用 AstrBot 当前的大模型 Provider 生成。

## 升级

在 `/persistent/astrbot/data/plugins/astrbot_plugin_proactive/` 替换 `main.py`、`_conf_schema.json` 和本文件，将 `voice_local.py` 放在 `main.py` 同一目录。把已有 `metadata.yaml` 的 `version` 改为 `v1.1.0`，然后重载插件。现有运行状态会被读取，旧状态文件会补上 `recent_topics`。

## 管理员指令

| 指令 | 作用 |
| --- | --- |
| `/主动` | 切换运行开关 |
| `/主动 开` | 打开运行开关，需要配置总开关已开启 |
| `/主动 关` | 关闭运行开关 |
| `/主动 状态` | 查看两层开关、今日条数、下次最早时间、距上次发送的时间、语音开关和当前性欲值 |
| `/主动 现在` | 立即尝试发送，跳过随机等待、发送间隔、空闲间隔和每日上限。仍遵守静默时段和两层开关 |

## 定时规则

默认每次成功发送后均匀抽取 40 到 150 分钟的间隔。定时发送还要求今日少于 12 条、主人最近 25 分钟没在私聊说话、距上次发送至少 40 分钟，并且不在 02:00–08:30 静默时段。静默时段可以跨午夜，起止相同时全天允许发送。容器时区是 UTC，插件按 `utc_offset_hours` 计算静默、日期和日志时间，默认是北京时间。

首次启动等待随机间隔。有发送记录时，从上次成功发送的时间续算，已到期时在启动后等 60 秒再检查。`/主动 现在` 成功后也计入今日条数，并重新抽取下一次间隔。

## 话题和性欲状态

`topics` 默认有十个方向，按早上、下午、晚上和深夜调整抽取权重，并避开最近三个方向。自定义列表太短而无可选话题时，从最早的排除项起逐步放开。正文通过校验后才记录本次方向，最多保留十个。

`use_libido` 默认开启。每次生成前读取 `astrbot_plugin_libido` 的状态文件和当次 `mood_line()`。读取或加载失败时照常生成。状态指令显示当前值，读取不到时显示「未知」。

## 语音

`with_voice` 默认开启，正文默认最多 60 字可加语音。由 `voice_dir` 指定模型根目录，`voice_name` 留空时按名称排序选第一个带 ONNX 的子目录。可显式填写 `kokoro`、`melo` 或 `aishell3`。模型首次使用时加载，后续复用。合成使用环境中已有的 sherpa-onnx 和 NumPy，WAV 和文字作为同一条消息发送。

`remote_tts_urls` 默认为空，只走本地。填写地址时，插件会先尝试飞鱼的远程合成，整个远程合成最多等 10 秒，失败再走本地。语音准备失败时仍发文字。

## 文件

- 状态文件是 `/persistent/astrbot/data/plugin_data/astrbot_plugin_proactive_state.json`。手工操作运行开关时，可以原子替换该文件，只改 `enabled`，保留其余字段。
- `/android-share/qq-turns.jsonl` 提供最近八条有效私聊，确认发送成功后追加文本流水。
- `/android-share/proactive-voice/` 保存按毫秒时间戳命名的 WAV。
- `/android-share/proactive-log.jsonl` 保留最近 200 行。新记录的 `libido` 是整数或 `null`，`voice` 是 `local`、`remote`、`fail:原因` 或 `null`。未尝试语音时记为 `null`。
