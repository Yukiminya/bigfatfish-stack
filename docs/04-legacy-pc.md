# 04 · 旧电脑层：DSH 桌面版 + dsh-qq-access

这是雪糕鱼最早的样子：电脑上跑 DSH 桌面版，`dsh-qq-access` 插件托管 NapCat，
QQ 消息经 HTTP 双通道进出。现在已被手机层取代，配置留在 `legacy-pc/` 里备查。

## 它是怎么工作的

```
QQ → NapCat（Windows 上跑，托管模式）
   ├─ 事件：POST → http://127.0.0.1:5701   （插件开的本地事件服务）
   └─ 接口：GET  → http://127.0.0.1:3000   （NapCat 的 OneBot HTTP）
        ↑↓
dsh-qq-access 插件（跑在 DSH 里）
   ↓
DSH 的模型与工具链（含 qq_web_search / qq_web_extract / qq_generate_image）
```

关键配置都在 `legacy-pc/qq-access-config.json.example`（已脱敏）：

| 字段 | 含义 |
|---|---|
| `selfId` | 机器人小号 |
| `ownerIds` | 主人（只有这些号能下指令） |
| `onebot.apiUrl` / `token` | 取接口的地址与 token（默认 `127.0.0.1:3000`） |
| `events.host` / `port` / `secret` | 事件服务（默认 `127.0.0.1:5701`） |
| `access.blockedUsers` / `blockedGroups` | 黑名单，机器人号 + 那个已退出的群 |
| `reply.group = "smart"` | 群聊「智能接话」：被叫到或话题相关才回 |
| `reply.selfNames` | 叫到这些名字算叫它：大肥鱼 / 雪糕鱼 / 鱼 / 肥鱼 |
| `rate.*` | 分段发送节奏：每条 1~10 秒、每分钟最多 20 条 |
| `life.*` | 主动说话：私聊开着，每天最多 5 条 |
| `image.*` | 豆包生图（`qq_generate_image`） |

## 为什么它跨设备用不了

`dsh-qq-access` 把 OneBot 地址**焊死在回环**：`lib/config.mjs` 里有断言，只接受
`127.0.0.1` / `localhost` / `[::1]`；事件服务也写死 `127.0.0.1`。
所以「电脑上的 NapCat + 手机上的插件」这种组合必然不通——这也是整条链搬到手机上的原因：
**只有把 NapCat 和插件放同一台设备，回环才成立**。

## 起它需要什么

1. Windows 电脑（NapCat 托管模式只支持 Windows x64）
2. DSH 桌面版 + `dsh-qq-access` 插件
3. 装 QQ NT（NapCat 挂它）
4. 把 `legacy-pc/qq-access-config.json.example` 复制成 `~/.dsh-qq-access/config.json`，
   填上你自己的 `token` / `secret` / `webuiToken`
5. 人设：`astrbot/persona/desktop-qq-persona.txt`
6. 聊天数据在 `~/.dsh-qq-access/data/messages/*.jsonl`，想法在 `data/thoughts/thoughts.json`，
   **这些不在仓库里，要自己从旧机器上备份**

## 什么时候值得回到它

- 你需要 `qq_generate_image`（豆包生图）、B 站/小黑盒专用工具链——这些**没有**跟着搬到 AstrBot。
- 你需要 DSH 的会话记忆、念头、动作回执——格式和 AstrBot 不兼容，搬不过去。
- 你的手机撑不住、或者不想让 QQ 长期挂在手机上。

## 别两边一起开

旧电脑层和手机主层**不能同时收同一个号的消息**，否则互相抢、重复回话。
切换时：先把一边的 OneBot 通道（或另一个平台的 `enable`）关掉，再启另一边，最后发条消息验证
只有一边回了。
