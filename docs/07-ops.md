# 07 · 日常运维

## 每天会用到的一条

```bash
bash scripts/verify-phone.sh
```

只读，不改东西。全 PASS 就说明「服务在、插件齐、人设在、NapCat 接着」。

## 改人设

三种改法，**选一种，别混着改**：

| 想要 | 怎么做 |
|---|---|
| 只改文字、要留版本 | AstrBot WebUI → 人设面板（`persona_manage` 插件），改完自动留时间戳快照 |
| 直接写库（可脚本化） | `PERSONA_IDS=default python scripts/apply-persona.py . /persistent/astrbot/data/data_v4.db` |
| 只改「用哪个人设」 | 主配置 `agent_runner.config.persona.persona_id` |

改完确认：私聊发一句，看语气是不是新版本。**改人设不需要重启**（每次请求都会读），
但改 `persona_id` 指向需要重启。

## 装/更新插件

```bash
# 手机里
cd /android-share/bigfatfish-stack
bash scripts/deploy-phone.sh          # 新增的装，已存在的跳过
FORCE=1 bash scripts/deploy-phone.sh  # 连已存在的一起覆盖
```

然后重启 AstrBot。**插件目录名必须是 `astrbot_plugin_<name>`**，改名 = 卸载 + 新装。

## 看日志

```bash
# AstrBot 自己的日志（默认不落盘，log_file_enable=false）
# 1) 打开落盘：主配置 log_file_enable=true，重启
tail -f /persistent/astrbot/data/logs/astrbot.log

# 2) 插件自己写的
tail -f /android-share/qqcall.log          # 语音来电
tail -f /android-share/coo_memory_debug.log
tail -f /android-share/proactive-log.jsonl

# 3) NapCat
tail -f /persistent/napcat/logs/*.log
```

想在电脑上看，用 `adb shell` 直接 tail，或者把 es.txt 拉到电脑：

```powershell
adb pull /sdcard/Android/data/io.astrbot.android/files/shared/qqcall.log .
```

## 重启

| 重启什么 | 怎么做 | 影响 |
|---|---|---|
| AstrBot | App 首页停止再启动，或 `pkill -f astrbot` 后按启动脚本起 | 断消息 10~60 秒 |
| NapCat | App 首页 → NapCat → 重启 | QQ 要重新验证一次（正常情况会自动重连） |
| 全部 | App 首页「启动全部」 | 最稳 |

改完任何配置/插件都**要重启 AstrBot**；只改人设文字不用。

## 该备份什么

按重要度排序，全都在手机里：

| 路径 | 是什么 | 丢了会怎样 |
|---|---|---|
| `/persistent/astrbot/data/data_v4.db` | 人设、会话、偏好 | 人设和对话上下文全没 |
| `/persistent/astrbot/data/config/` | 12 个插件的配置 | 行为参数回到默认 |
| `/persistent/astrbot/data/cmd_config.json` | 主配置（含 API Key） | 要重新填模型和平台 |
| `/persistent/napcat/config/` | NapCat 网络配置 | 要重新配 WS |
| `/android-share/tts/` | 语音缓存 | 无所谓，会重新生成 |
| `/android-share/qq-turns.jsonl` | 对话流水 | 桌宠那边的记忆断档 |

一条命令备份（电脑侧）：

```powershell
$adb = "adb"
$dst = "D:\backup\bigfatfish-$(Get-Date -Format yyyyMMdd)"
New-Item -ItemType Directory -Force $dst | Out-Null
$sh = "/sdcard/Android/data/io.astrbot.android/files/shared"
# 用 execrelay 在手机里打个包再拉下来最省事（见 docs/05-plugins.md 的 execrelay 段）
```

或者直接用 execrelay：

```json
{"cmd":"mkdir -p /android-share/bak && tar czf /android-share/bak/data-$(date +%Y%m%d).tgz -C /persistent/astrbot data/data_v4.db data/config data/cmd_config.json && ls -l /android-share/bak","timeout":180,"seq":1}
```

## 回滚

1. **人设写坏了**：`data/plugin_data/astrbot_plugin_persona_manage/*.txt` 里有时间戳快照，
   挑一个贴回去；或者 `scripts/apply-persona.py` 会先备份整张表到
   `personas-backup-<时间>.json`。
2. **配置改坏了**：`scripts/apply-config.py` 每次写之前都会生成 `cmd_config.json.bak-<时间>`，改回去重启。
3. **插件更新炸了**：`deploy-phone.sh` 不覆盖已存在目录，所以旧版本可能还在——如果没有，
   从本仓库 `astrbot/plugins/` 重新铺一遍。

## 安全清单（定期看一遍）

- [ ] `ASTRBOT_WEB_PASSWORD` 不是默认值，且 6185 只绑 127.0.0.1
- [ ] `NAPCAT_WEBUI_TOKEN`、`ASTRBOT_WS_TOKEN` 不是仓库里的占位符
- [ ] 调试完把 `execrelay` 停掉（它是文件触发的任意命令执行）
- [ ] `qq_voice_call` 的 `voice_uid_whitelist` 填了主人
- [ ] DeepSeek Key 没写进任何会公开的文件
- [ ] `qq-access-config.json` 里的 `access.blockedGroups` 含已退出的群

## 花了多少钱

AstrBot 后台有 token 用量面板；`proactive` 每天最多 12 条主动消息，是最容易被忽略的消耗项。
觉得贵就先把 `daily_cap` 降到 3，再看一周。
