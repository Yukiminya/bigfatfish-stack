# 05 · 13 个插件逐个说

全部在 `astrbot/plugins/`，行为以源码为准。装法见 `scripts/deploy-phone.sh`，配置落在
`/persistent/astrbot/data/config/astrbot_plugin_<name>_config.json`。

一句话总览：

| 插件 | 一句话 | 有没有配置 |
|---|---|---|
| `android_hands` | 读屏点屏，自己刷 B 站/小黑盒/小红书 | 有 |
| `android_observer` | 只读的连接健康观测 | 无 |
| `coo_memory` | 把对话写在共享队列，给电脑上的桌宠 Coo 用 | 有 |
| `execrelay` | **开发期**文件触发执行中继（危险） | 无 |
| `feiyu_voice` | 把回复合成语音条 | 有 |
| `hb` | 心跳测试：加载时写 `hb.txt` | 无 |
| `libido` | 一个自己走的 0-100 数值 + 五档语气 + 面板 | 有 |
| `memprobe` | 探针：把钩子/字段/目录结构写成 json | 无 |
| `persona_manage` | 人设提示词与历史版本管理（Web 面板） | 有 |
| `persona_probe` | 证明人设真的进了第一条 system 消息（只写日志） | 无 |
| `proactive` | 不定时主动发私聊（带话题轮换、语音） | 有 |
| `probe` | 一次性 HTTP 探针（测电脑 TTS 通不通） | 无 |
| `qq_voice_call` | 接 QQ 私聊语音来电，实时对话 | 有 |

---

## android_hands · 雪糕鱼的手

**干什么**：通过 DSHA 的 3091 设备桥读屏、点按、输入，自己打开 B 站 / 小黑盒 / 小红书，
滑动、看内容、写评论、把有趣的截图转发给主人。也能顺手做本地 ASR（`local_asr.py`）。

**依赖**：DSHA 设备桥在 `127.0.0.1:3091` 上可用，且**识屏模式已打开**（否则所有 `/app/ui/*`
返回 `SCREEN_MODE_OFF`）。不需要 root/Shizuku/ADB。

**关键配置**（`astrbot_plugin_android_hands_config.json`）：

```json
"timeout": 25,            // 单次桥调用超时
"max_elements": 60,       // 读屏返回节点上限
"watch_min" / "watch_max": 20 / 45,        // 单次「看」多久
"auto_*": 间隔/时段/每日上限/跳过概率      // 自动行为的节奏
"roam_apps": "bilibili,xiaoheihe,xiaohongshu",
"roam_hour_start" / "roam_hour_end": 8 / 24,
"roam_daily_limit": 8,
"roam_comment_probability": 30,            // 写评论的概率（%）
"roam_forward_probability": 80,            // 转发给主人的概率（%）
"voice_asr_enabled": false,                // 语音识别默认关
"voice_asr_base": "https://api.siliconflow.cn/v1",
"voice_asr_key": ""                        // 用云端 ASR 才填，占位 ${SILICONFLOW_API_KEY}
```

**坏了怎么查**：
1. 桥通不通：`curl -H @/root/.dsh/.bridge_headers http://127.0.0.1:3091/app/version`
2. 识屏开没开：`/app/ui/task-begin` 返回 `SCREEN_MODE_OFF` 就是没开（去设置里开「实体识屏」）
3. 手机是不是锁屏了——锁屏什么都点不动

---

## android_observer · 连接观测

**干什么**：只读地报告 Android 侧的连接健康状态。禁用它，「连接状态」就变成未知（不是「坏」）。

**注意**：`metadata.yaml` 里的 `name` 是 `android_connection_observer`，但**目录名**是
`astrbot_plugin_android_observer`——AstrBot 认目录名，别按 name 去删除/启用。

---

## coo_memory · 和电脑上的桌宠共享记忆

**干什么**：把 QQ 对话写进共享队列 `/android-share/qq-turns.jsonl` 交给电脑上的桌宠 Coo；
再把 Coo 的记忆注进系统提示词——一条记忆，两边都记着。

**关键配置**：

```json
"enable": true, "record": true, "inject": true,
"max_inject_chars": 2500,       // 注入上限，别调太大，会顶掉人设
"share_dir": "/android-share"
```

**坏了怎么查**：看 `/android-share/coo_memory_debug.log` 和 `qq-turns.jsonl` 有没有新行；
电脑侧没在读也不影响机器人自己聊天，只是「共享」这半边断了。

---

## execrelay · 开发期执行中继（危险，务必知道）

**干什么**：每 0.5 秒读 `/android-share/exec.json`，在 AstrBot 的 Python 环境里执行，结果写
`/android-share/exec_res.json`。支持两种模式：

```json
{"cmd": "shell 命令", "timeout": 60, "seq": 1795391943268}     // shell=True
{"py":  "python 代码", "timeout": 60, "seq": 1795391943269}    // python -c
```

`seq` 用来防止重复执行——**同一个 seq 只跑一次**，所以每次要么换新 seq，要么把 exec.json 删掉。

**风险**：任何能写 `/android-share` 的东西（包括任何一个 App）都能借它拿到 AstrBot Python 环境的
任意命令执行。这是调试用的后门，**部署完、调完就该停用**。

**顺带说**：本仓库的 `scripts/push-to-phone.ps1` 就是靠它实现「电脑一条命令让手机跑脚本」。
不需要这条通道的话，把插件停用，改用 `adb shell` 手动跑。

---

## feiyu_voice · 嗓子

**干什么**：每条回复后面跟一条几秒的语音条。优先用**电脑上的 GPT-SoVITS**
（adb 隧道 / 局域网 / 云端三路回退），全失败才退回手机本地 VITS 或 edge-tts。

**关键配置**：

```json
"enabled": true,
"probability": 1.0,           // 触发概率
"max_chars": 120,             // 超过就不念（省算力）
"speed": 1.0,
"voice_dir": "/android-share/tts",
"remote_urls": "http://127.0.0.1:9880,http://192.168.1.7:9881,edge:zh-CN-XiaoyiNeural",
"voice_whitelist": "641961016"   // 只有这些人能听到语音
```

**坏了怎么查**：
1. `probe` 插件或 `curl` 打一下 9880，看有没有返回音频
2. `voice_dir` 里有没有新 wav（有 wav 但没收到 = 发送环节的问题）
3. 电脑上的 GPT-SoVITS 是不是没起 / 模型没加载

---

## hb · 心跳

**干什么**：插件加载时写 `/android-share/hb.txt`。**纯测试用**，用来确认「AstrBot 真的重新加载了
这个插件」「共享目录还能写」。文件会越来越大，属于正常。

---

## libido · 状态数值

**干什么**：一个会自己走的 0-100 数值，五档语气（平静 / 有点想 / 难耐 / 快撑不住 / 发情），
每月 18–20 日是发情期，附一个浏览器面板。`proactive` 会读它来调语气。

**关键配置**：

```json
"enabled": true,
"initial": 20,
"tick_seconds": 300,      // 每 5 分钟走一次
"panel_port": 41888       // 面板端口，adb forward 出来看
```

**坏了怎么查**：`adb forward tcp:41888 tcp:41888` 后开 <http://127.0.0.1:41888>；
数值不动就是 tick 没跑（看日志有没有异常）。

---

## memprobe · 能力探针

**干什么**：把「有哪些插件钩子 / `ProviderRequest` 有哪些字段 / 数据目录长什么样 /
会话库什么结构」写到 `/android-share/memprobe.json`。写插件前先跑它，能省掉猜 API 的时间。

**坏了怎么查**：文件没出现 = 插件没加载；文件是旧的 = AstrBot 没重启。

---

## persona_probe · 人设到底进没进提示词

**干什么**：不改变任何请求，只在 `on_agent_begin` 里核对一件事——「按人设拼出来的那段
`\n# Persona Instructions\n\n<人设全文>\n`，是不是逐字出现在**第一条 system 消息**里」。
答案只写一行 INFO 日志。

**怎么读这行日志**（WebUI → 数据与日志 → 日志，过滤 `Persona probe`）：

```
Persona probe status=matched role=system persona_id='default' source=persona message_index=0
```

| 字段 | 含义 |
|---|---|
| `status=matched` | 找到了一字不差的注入文本；`missing` = 没进提示词；`no_persona_trace` = 这次请求没带人设 ID |
| `role=system` | 位置是系统级消息，不是 user 消息 |
| `persona_id` | 用的哪个人设（`agent_runner.config.persona.persona_id` 指的那个） |
| `source` | `persona` = 人设本体；`webchat_default` = WebChat 的兜底人设 |
| `message_index=0` | 在消息列表里的下标，0 就是第一条 |

**为什么值得留着**：改完人设之后，「后台看着改了、聊天里却没变」是最常见的坑。
这行日志把「我以为注入了」变成「注入确实发生了」。

**已知坑**：Codex 写这个插件时漏了 `@register(...)` 装饰器，插件目录在、日志也在、
但插件根本没被加载（`/api/plugin/get` 里查不到）。补上装饰器重启后才有输出——所以
`main.py` 第 9 行那行 `@register(...)` 是后补的，不是原稿。

**开销**：只在真机 request 时跑一次字符串 `in` 判断，人设 2 KB 量级，可忽略。

---

## persona_manage · 人设提示词管理

**干什么**：在 Web 面板和聊天里管理**人设提示词与历史版本**。它会在
`/persistent/astrbot/data/plugin_data/astrbot_plugin_persona_manage/` 下留带时间戳的快照，
写坏了能回滚到某个版本。

**和 AstrBot 自带人设的关系**：AstrBot 自己的人也设在 `data_v4.db` 的 `personas` 表；
这个插件管的是「提示词文本 + 版本」。两边都能改人设，**别同时改**，容易自己覆盖自己。

**后台里的页面入口（2026-11-23 加）**：插件目录下多了 `views/persona/index.html`，用的是
AstrBot 4.29.3 自带的「插件页面」机制——后台扫到 `views/<页面名>/index.html` 就会把它挂出来，
侧边栏直接多一项「人设提示词管理」，点开就是编辑器（字数/行数/估算 token、人设下拉、
历史版本与一键回滚）。页面里不直连 `/api/plug/...`，而是走后台注入的 bridge
（`window.AstrBotPluginView.apiGet/apiPost`），所以写入一定同源、不会因为
`sec-fetch-site` 被 403。页面地址是 `/#/plugin-view/astrbot_plugin_persona_manage/persona`。

**为什么不能直接开面板 URL**：`/api/plug/astrbot_plugin_persona_manage/panel` 需要后台登录态，
裸链接打开只会得到 `{"status":"error","message":"未授权"}`——这是设计如此，不是坏了。

**坏了怎么查**：面板打不开 → 插件没加载或端口冲突；改了不生效 → 确认
`agent_runner.config.persona.persona_id` 指向的那个人设是你改的那个。

---

## proactive · 不定时主动发消息

**干什么**：「雪糕鱼」按随机间隔主动给主人发 QQ 私聊，带话题轮换、状态联动、语音条，
支持静默时段、每日上限、管理员开关。

**关键配置**：

```json
"enabled": true,
"min_gap_min" / "max_gap_min": 40 / 150,     // 两条之间的随机间隔
"quiet_start" / "quiet_end": "02:00" / "08:30",
"daily_cap": 12,
"idle_gap_min": 25,                          // 对方多久没说话才算「可以主动」
"max_chars": 60,
"use_persona": true,
"target_umo": "napcat:FriendMessage:641961016",   // ⚠ 换成你自己的号
"use_libido": true,
"with_voice": true,
"remote_tts_urls": "http://127.0.0.1:9880,http://192.168.1.7:9881,edge:zh-CN-XiaoyiNeural"
```

**坏了怎么查**：
1. `target_umo` 里的号是不是你自己的（写错就发给别人，或者干脆不发）
2. 现在是不是在静默时段
3. `/android-share/proactive-log.jsonl` 有没有记录，`proactive-check.json` 里 `skip_reason` 是什么

---

## probe · 一次性探针

**干什么**：从手机往电脑的 TTS 打一次 HTTP，把结果写出来。用来判断「手机能不能连到电脑的 9880」。
排嗓子问题第一步就该跑它。

---

## qq_voice_call · QQ 语音来电

**干什么**：接听 QQ 私聊语音来电，用本地 ASR + 大模型 + GPT-SoVITS 实时对话（**只接听，不拨号**）。

**关键配置**：

```json
"ws_host": "127.0.0.1", "ws_port": 6198, "ws_path": "/qq-voice-call",
"ws_token": "…",                 // 占位 ${QQVOICE_WS_TOKEN}，和 NapCat 侧一致
"voice_whitelist" / "voice_uid_whitelist": "",   // 空 = 谁都能打进来，建议填主人
"auto_accept": true,
"tts_url": "http://127.0.0.1:9880",
"asr_model": "/android-share/asr/model.int8.onnx",
"asr_tokens": "/android-share/asr/tokens.txt",
"speaker_monitor": "qqspk.monitor", "mic_sink": "qqmic",
"max_reply_chars": 60
```

**依赖比较硬**：ASR 模型必须放在 `/android-share/asr/`（见 `napcat` 里的音频通路配置），
`tts_url` 必须通，音频虚拟设备（`qqspk.monitor` / `qqmic`）必须存在。

**坏了怎么查**：看 `/android-share/qqcall.log`；先确认 6198 有人在听、NapCat 侧转发开着，
再确认 ASR 模型文件在。

---

## 装/停用的正确姿势

```bash
# 手机里
cd /android-share/bigfatfish-stack
bash scripts/deploy-phone.sh          # 幂等，已存在的不覆盖
FORCE=1 bash scripts/deploy-phone.sh  # 用仓库版本覆盖

# 临时停用一个插件
mv /persistent/astrbot/data/plugins/astrbot_plugin_hb \
   /persistent/astrbot/data/plugins/astrbot_plugin_hb.disabled
# 然后重启 AstrBot
```

**改完一定要重启 AstrBot**，插件是在启动时加载的。
