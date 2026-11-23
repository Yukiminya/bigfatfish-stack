# 03 · DSHA 层：手机上的操作台

DSHA（[DSH-APP/DSHA](https://github.com/DSH-APP/DSHA)）是把**完整 DeepSeek Harness** 打进
Android 的应用：内置 Ubuntu + Node + `@deepseek-ai/dsh`，装完即用，免 root 免 Termux。

它和 QQ 机器人**不是一回事**，别指望装完它就有了雪糕鱼。

## 它到底给什么

| 能力 | 说明 |
|---|---|
| 完整 DSH | `/root/.dsh`，`DSH_HOME` 指向它，profile 自选（例：`web`），Web UI 在 127.0.0.1:37483 |
| 设备桥 | 3091，`BRIDGE_PROTOCOL=3`，header `X-Token` 鉴权，凭据 `/root/.dsh/.bridge_headers` |
| 读屏/点屏 | `/app/ui/dump`、`/app/ui/tap`、`/app/ui/input`、`/app/ui/key`，走无障碍，**不需要 root/Shizuku/ADB** |
| 读文件 | `/app/readfile?path=...`，容器路径或共享存储，≤256 KiB |
| 共享存储 | DSHA 也能看到 `/sdcard`，和 AstrBot 的 `/android-share` 是同一块地 |

桥只在 127.0.0.1 上。电脑要连它，用 `adb forward`：

```bash
adb forward tcp:23091 tcp:3091
curl -H "X-Token: <token>" http://127.0.0.1:23091/app/version
```

token 在手机里：

```bash
# 手机 proot 里（DSHA 的容器）
cat /root/.dsh/.bridge_headers     # 形如：X-Token: <32 位>
```

用 `curl -H @/root/.dsh/.bridge_headers` 引用它，别把 token 写进命令行历史。

## 为什么要它

因为 AstrBot 自己**碰不到手机**。它是个跑在容器里的 Python 进程，能算能说，但看不见屏幕。
`astrbot_plugin_android_hands` 就是靠调 3091 桥来实现「自己刷 B 站、看小黑盒」。

## 铺 `dsha/` 里的资产

```bash
bash scripts/deploy-dsh-assets.sh --profile-dir ~/.dsh/profiles/web
```

（手机上跑时把 `--profile-dir` 指到 DSHA 的 profile 目录，一般是 `/root/.dsh/profiles/web`。）

这个脚本只做文件安置 + 备份，不猜 DSH 的安装命令。铺完你还要做两件事：

1. 打开 `~/.dsh/profiles/web/package.json`，确认 `dsh.profile.bundles` 里有你需要的插件；
2. 启动 DSH（DSHA 里就是点启动），它自己会把 profile 依赖装上。

`dsha/` 里的东西：

| 路径 | 是什么 | 备注 |
|---|---|---|
| `dsh-profile/package.json` | 电脑上那份 profile 的依赖与 bundles 清单 | 换机器要按新机器改 `link:` 路径 |
| `dsh-profile/cordis.patch.yml` | 各插件的配置覆盖、禁用项 | **含绝对路径**（gh/git/node），必须改 |
| `dsh-profile/cordis.yml` | profile 根，空列表 | 别手改，改 patch 层 |
| `dsh-profile/pnpm-workspace.yaml` | workspace 声明 | — |
| `skills/codex-web-computer-use/` | 让 DSH 用 Computer Use 派活给网页端 Codex 的技能 | 纯文档 |
| `skills/video-watch-plus/` | 看视频/听音频的分析技能（含 vendored 工具） | 纯文档 + 脚本 |
| `plugins/dsh-persona-manage/` | 人设管理插件源码（含 WebUI 面板） | 装法是 npm/github 依赖，不是丢进目录 |
| `plugins/voice-wake/` | 语音唤醒插件源码 | `@local/voice-wake` 是 `link:` 依赖 |

> `cordis.patch.yml` 里这几处绝对路径必须按目标机器改：
> `forge-github.ghCommand`、`worktree.gitCommand`、`worktree.nodeCommand`。

## 装人设（这一步才让 DSHA「像她」）

DSHA 里的 DSH 跑的是通用编码智能体，不是雪糕鱼。要它带人设：

1. 装好 `dsh-persona-manage`；
2. 打开它的管理页，把 `astrbot/persona/desktop-qq-persona.txt`（电脑版语气）或
   `astrbot/persona/default.txt`（手机版语气）贴进去；
3. 保存后 `cordis.patch.yml` 的 `system-prompt` 段会被插件改成空，由插件注入——这是正常现象，
   见仓库里 `dsh-profile/cordis.patch.yml` 末尾那段 `>>> dsh-persona-manage (managed) >>>`。

## 边界（别越界）

- **同一时刻只有一个东西在收 QQ 消息。** 手机 AstrBot 在用，就别把电脑端 `dsh-qq-access` 也开着。
- **锁屏 = 自动化全废。** 屏幕必须亮且解锁，`/app/ui/*` 全挂。
- 手机任务有门禁：每次手机操作前要 `/app/ui/task-begin`，带真实的 `taskSessionId`/`turnId`。
  未验证：QQ 消息触发的轮次能不能过这道门。

## 卸载/回滚

DSHA 与 AstrBot 互不影响：卸 DSHA，QQ 机器人照跑；反过来也一样。
DSHA 的数据都在它自己的容器里，卸 App 就没了——重要东西别只存在里面。
