# 02 · 手机快速上手：从裸机到能回话

预计 40~60 分钟，其中 10 分钟是等 App 装环境。**第一次做请在电脑旁边**，配置在电脑上做快得多。

## 0. 准备清单

- [ ] 安卓手机，Android 8+，6G 内存起，能长期插电联网
- [ ] 一个 **QQ 小号**（不要用主号当机器人）
- [ ] 一个 **DeepSeek API Key**：<https://platform.deepseek.com/api_keys>
- [ ] 电脑一台（同 Wi-Fi），装了 `adb`（可选但强烈建议）
- [ ] 本仓库（`git clone` 或下载 zip）

## 1. 装 App 并初始化

用「AstrBot for Android」这类把 AstrBot + NapCat + Linux QQ 打进同一个 proot Ubuntu 的包。

1. 装 APK（允许未知来源）。
2. 打开 App → 「初始化环境」。它会解包内置 Ubuntu、装 AstrBot 和 NapCat。**首次要几分钟，
   别切后台**。
3. 初始化完先点一次「启动全部」，让 AstrBot 把 `/persistent/astrbot/data` 建出来——
   后面所有脚本都依赖这个目录存在。

## 2. 后台保活（不做这步，锁屏就死）

不同品牌路径不同，照提示点，本质是四件事：

- 电池优化 → **无限制**（vivo：设置 → 电池 → 后台耗电管理 → 允许后台高耗电）
- 自启动 → 允许
- 最近任务里给 App **加锁**，别被一键清理带走
- 屏幕：**充电时常亮**。锁屏 = 无障碍与自动化全废，这不是省钱的地方

## 3. 打通电脑到手机的两条通道

### 3.1 AstrBot WebUI（用浏览器点配置）

```bash
adb forward tcp:16185 tcp:6185
```

然后电脑浏览器开 <http://127.0.0.1:16185>，用户名 `astrbot`，密码在 App「AstrBot → 详情 → 连接」里。

### 3.2 命令行执行通道（可选，但用了就回不去）

手机上的 `astrbot_plugin_execrelay` 会在共享目录里收命令，用 AstrBot 的 Python 环境执行：

```bash
adb shell "cat /sdcard/Android/data/io.astrbot.android/files/shared/exec_res.json"
```

写命令进 `exec.json`（`{"cmd":"...","timeout":60,"seq":<毫秒时间戳>}`），回执在 `exec_res.json`，
`seq` 对得上才算这次的结果。脚本 `scripts/push-to-phone.ps1` 封装了这套。

## 4. 填配置

```bash
cp .env.example .env
```

至少要改这几个：

| 变量 | 填什么 |
|---|---|
| `DEEPSEEK_API_KEY` | `sk-` 开头的那串 |
| `QQ_SELF` | 小号 QQ 号 |
| `QQ_OWNER` | 你自己大号 QQ 号（只有它能当主人） |
| `ASTRBOT_WS_TOKEN` | 自己编一串随机字符，两端填一样 |
| `ASTRBOT_WEB_PASSWORD` | WebUI 密码 |
| `TTS_URLS` | 有 GPT-SoVITS 就填地址，没有就留 `edge:zh-CN-XiaoyiNeural` |

## 5. 部署

把仓库推到手机，然后在手机里跑脚本。两种方式，选一种：

**A. adb 一键（推荐，Windows）**

```powershell
.\scripts\push-to-phone.ps1 -AdbPath "adb" -Deploy -Verify
```

它做三件事：`adb push` 仓库到 `/sdcard/.../shared/bigfatfish-stack`、写 `exec.json` 触发
`scripts/deploy-phone.sh`、再触发 `scripts/verify-phone.sh`。

**B. 手动**

```bash
# 手机里（proot Ubuntu，root）
cd /android-share/bigfatfish-stack
bash scripts/deploy-phone.sh
bash scripts/verify-phone.sh
```

`deploy-phone.sh` 会：装 12 个插件 → 铺插件配置 → 合并主配置（模型/平台/限速）→ 写人设 →
写 skills 清单 → 铺 NapCat 配置。**默认不覆盖已存在的配置**，要覆盖就 `FORCE=1`。

## 6. 扫码登录 QQ

1. App 首页 → NapCat → 「打开后台」→ 扫码登录小号。
2. 登录后确认 NapCat 已经连上 AstrBot：

```bash
bash scripts/verify-phone.sh
# 期望看到：PASS  反向 WS 服务在 6199 监听
#          PASS  有 websocketClient -> ws://127.0.0.1:6199/ws
```

## 7. 第一次说话

1. 用大号私聊小号发「在吗」。
2. 期望：几秒内回一条短句（不用句号、不像客服）。
3. 不回就按顺序查：
   - `verify-phone.sh` 有没有 FAIL
   - AstrBot WebUI → 日志，看有没有 `OneBot` 事件进来
   - `data/cmd_config.json` 里 `provider_sources[0].key` 有没有真的写进去
   - NapCat WebUI 里 websocketClient 是不是 `enable: true`

## 8. 让它更像「她」

| 想要 | 怎么做 |
|---|---|
| 说话短、分条 | 主配置 `platform_settings.segmented_reply.enable = true` |
| 有嗓子 | 起电脑上的 GPT-SoVITS，把地址填进 `feiyu_voice` 配置的 `remote_urls` |
| 会主动找你 | `proactive` 插件已默认开：40~150 分钟随机、02:00–08:30 静默、每天上限 12 条 |
| 会自己刷 B 站 | 需要 DSHA（见 docs/03-dsha.md） |
| 不跟机器人说话 | 人设 + `astrbot/persona/bot-blocklist.md`，两个都要 |

## 常见卡点

- **扫完码秒掉线**：QQ 风控。换设备登录容易触发（serverErrorCode 168），等一会儿再扫，
  或者先用手机 QQ 正常登录一次小号再让 NapCat 扫。
- **WebUI 打不开**：6185 默认只绑 127.0.0.1，必须 `adb forward`，别指望局域网直连。
- **插件装了不生效**：AstrBot 要重启；且插件目录名必须是 `astrbot_plugin_xxx`。
- **`deploy-phone.sh` 说没有 venv**：AstrBot 还没初始化完，或者 `ASTRBOT_HOME` 不是
  `/persistent/astrbot`（用环境变量覆盖它）。
