# 01 · 架构与数据流

一句话：**QQ → NapCat → AstrBot → DeepSeek → AstrBot → NapCat → QQ**，全都在一台手机里闭环。
DSHA 和电脑端是另外两条可选支线，不参与这条主链。

## 三层，别混

| 层 | 装的是什么 | 干什么 | 和 QQ 机器人的关系 |
|---|---|---|---|
| **手机主层** | AstrBot 4.29.3 + NapCat 4.18.41 + Linux QQ，跑在 Android 的 proot Ubuntu 里 | 机器人的全部行为能力 | **就是它本体** |
| **DSHA 层** | [DSHA](https://github.com/DSH-APP/DSHA)，手机上跑完整 DeepSeek Harness | 手机上改代码、读屏、点屏、跑 DSH 会话 | 操作台，不参与聊天 |
| **旧电脑层** | DSH 桌面版 + `dsh-qq-access` + NapCat | 最早的形态 | 已被手机层取代，留档 |

三层可以同时存在，但**同一个 QQ 号同一时刻只能有一层在收消息**，否则两边抢消息、重复回话。

## 主链：一条消息的完整旅程

```
别人在 QQ 里发「在吗」
   ↓  （腾讯服务器 → 手机上的 Linux QQ）
NapCat（proot 里，监听本机）
   ↓  websocketClient 反向连接 ws://127.0.0.1:6199/ws
AstrBot 的反向 WS 服务（platform: aiocqhttp）
   ↓  AstrBot 事件总线
插件链（按事件优先级，见 docs/05-plugins.md）
   ├─ coo_memory     记录这一轮，必要时注入记忆
   ├─ libido         更新「状态」数值，影响语气
   └─ persona_manage 决定用哪个人设
   ↓  组装 Prompt（人设 + 记忆摘要 + 近期上下文 + 状态）
DeepSeek API（deepseek-flash，非流式，超时 120s）
   ↓
AstrBot 拿到回复
   ├─ feiyu_voice   命中白名单 → 把文字丢给电脑上的 GPT-SoVITS 合成语音
   └─ 按「分段回复」规则切成多条
   ↓  OneBot v11 动作（send_private_msg / send_group_msg）
NapCat → Linux QQ → 对方收到（文字 + 语音条）
```

## 端口与路径对照表

| 端口 | 谁在听 | 谁连它 | 说明 |
|---:|---|---|---|
| **6185** | AstrBot WebUI | 你（电脑浏览器 / adb forward） | 后台管理，默认只绑 127.0.0.1 |
| **6199** | AstrBot 的反向 WS 服务 | NapCat 的 websocketClient | **消息主通道**，NapCat 主动连过来 |
| 6198 | `qq_voice_call` 插件 | NapCat 的语音通话转发 | 接 QQ 语音来电用 |
| **6099** | NapCat WebUI | 你 | 扫码登录、网络配置 |
| 41888 | `libido` 插件面板 | 你（浏览器） | 看/调那个 0-100 的数值 |
| 9880 / 9881 | 电脑上的 GPT-SoVITS | 手机上前两个语音插件 | 合成语音条；9881 是备用实例 |
| 3091 | DSHA 设备桥 | DSH 会话 / 你的 curl | 读屏点屏，**只绑 127.0.0.1** |
| 37483 | DSHA 里的 DSH Web | 你 | 手机上的 DSH 界面 |
| 3000 / 5701 | 旧电脑层 `dsh-qq-access` | 旧 NapCat | HTTP 取接口 / 事件上报，**只在电脑本机** |

容器内路径（proot Ubuntu）：

| 路径 | 是什么 |
|---|---|
| `/persistent/astrbot` | AstrBot 根目录（`ASTRBOT_HOME`） |
| `/persistent/astrbot/data/plugins` | 12 个插件的源码目录 |
| `/persistent/astrbot/data/config` | 每个插件的运行配置 `astrbot_plugin_<name>_config.json` |
| `/persistent/astrbot/data/cmd_config.json` | AstrBot 主配置（模型、平台、限速、WebUI） |
| `/persistent/astrbot/data/data_v4.db` | SQLite：人设、会话、偏好都在这里 |
| `/persistent/napcat` | NapCat 根目录（`NAPCAT_HOME`） |
| `/persistent/napcat/config` | `onebot11_<QQ>.json`、`webui.json` 等 |
| `/android-share` | 共享目录，= `/sdcard/Android/data/io.astrbot.android/files/shared` |
| `/opt/astrbot-venv` | AstrBot 的 Python 虚拟环境（插件跑在这里面） |

`/android-share` 是这个栈的**万能交换区**：插件往里写（语音、心跳、探针、执行回执），
电脑侧 `adb pull` 取走，或 `adb push` 放东西进去给插件读。

## 为什么主链必须是「NapCat 连 AstrBot」

NapCat 有两种接法：正向 WS（AstrBot 连 NapCat）和反向 WS（NapCat 连 AstrBot）。
这里用的是反向：NapCat 作为 client 连 `ws://127.0.0.1:6199/ws`。
好处是 AstrBot 只需要开一个监听，NapCat 重启后自己会重连（`reconnectInterval: 5000`），
不需要 AstrBot 跟着重启。配置文件里能直接看到这条：

```json
// /persistent/napcat/config/onebot11_<QQ>.json
"websocketClients": [
  { "name": "AstrBot", "enable": true, "url": "ws://127.0.0.1:6199/ws", "reconnectInterval": 5000 }
]
```

## 数据不在这里的地方

- **聊天记录**不在仓库里，也不在 AstrBot 的默认表里：`coo_memory` 把对话写进
  `/android-share/qq-turns.jsonl`，那是给电脑上的桌宠 Coo 读的队列。
- **语音缓存**在 `/android-share/tts`，`feiyu_voice` 和 `proactive` 都往这儿写 wav。
- **人设**在 `data_v4.db` 的 `personas` 表，不是文本文件。改人设要么用 WebUI，要么跑
  `scripts/apply-persona.py`。

## 与 DSHA 的关系（一句话）

DSHA 是**另一个 App**，有自己的容器、自己的 `/root/.dsh`、自己的 DSH 实例，和 AstrBot 的
proot 互不相通。它们唯一的物理交集是：都在同一台手机上、都能看到 `/sdcard` 上的共享目录。
所以「让雪糕鱼自己刷 B 站」这种事的真实路径是：
`android_hands` 插件 → 调 DSHA 的 3091 桥 → 桥用无障碍服务点屏幕。

> 未验证：QQ 消息触发的轮次能不能过 DSHA 的手机任务门禁（`task-begin` 需要
> `taskSessionId`/`turnId`）。历史会话里撞到的是 `SCREEN_MODE_OFF`，没验证到门禁那一层。
