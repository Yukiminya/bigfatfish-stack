# 10 · 灾难恢复演练（真机实测记录）

目标：证明「把手机上的相关文件删掉，用这个仓库装回来」真的能恢复功能。
判定标准只有一条：**删之前能聊，删之后能用这个包装回能聊**，而且旧上下文不掺进来。

本文的数字全部来自 2026-11-23 的一次真机演练，不是推演。

---

## 0. 前置

- 手机：vivo V2243A / Android 16，`io.astrbot.android` 在 proot Ubuntu 里跑（AstrBot 4.29.3 + NapCat 4.18.41 + Linux QQ）
- 电脑：能 `adb` 连上手机（本次序列号 `10AD4X0XL60026M`）
- 远程执行通道：手机上的 `astrbot_plugin_execrelay`（读 `/android-share/exec.json`）
- 仓库已 clone 到手机 `/android-share/dr-repo`，`.env` 填好真实值（`.env` 不进仓库）

> ⚠ `.env` 里有明文 API Key，且在 `/sdcard` 上其他 App 可读。演练完 `rm` 掉。

---

## 1. 备份（先做，不做完不许删）

```bash
bash scripts/dr-backup.sh
adb pull /sdcard/Android/data/io.astrbot.android/files/shared/backup/<TS> D:\bfs-backup\<TS>
```

**本次产物**

| 文件 | 大小 | 内容 |
|---|---:|---|
| `astrbot-data.tgz` | 66,384,109 B | 整个 `/persistent/astrbot/data`（db、插件、配置、plugin_data） |
| `napcat-config.tgz` | 12,276 B | `/persistent/napcat/config` |
| `qq-turns.jsonl` | 91,103 B | 与电脑桌宠 Coo 共用的对话流水 |
| `proactive-log.jsonl` | 26,339 B | 主动搭话记录 |
| `persona*.txt` | 3 个 | 人设的文本快照 |
| `SHA256SUMS` | — | 两个 tgz 的校验 |

电脑侧逐个核对：`astrbot-data.tgz OK`、`napcat-config.tgz OK`。

**上下文单独导一份**（人设与会话分开存，方便「只回人设不回记忆」）：

| 文件 | 内容 |
|---|---|
| `context/personas.json` | 两个人设全文（default 2071 字、dafeiyu 1236 字） |
| `context/conversation-friend-641961016.json` | 私聊会话正文 **3,187,447 B** |
| `context/conversation-group-972835876.json` | 群会话正文 **91,575 B** |
| `context/preferences.json` / `umo_aliases.json` | 5 条偏好、3 条会话别名 |

---

## 2. 删除

```bash
bash scripts/dr-reset.sh            # 默认保留 execrelay 当远程通道
bash scripts/dr-reset.sh --dry-run  # 只打印
```

安全设计：`/android-share/backup` 下没有含 `astrbot-data.tgz` 的备份时**直接拒绝执行**，
`--force` 也不绕过。

**本次删除前后**

| 项 | 删前 | 删后 |
|---|---:|---:|
| `data/plugins` | 12 | 1（只剩 execrelay） |
| `data/config/*.json` | 6 | 0 |
| `data_v4.db` | 6,782,976 B | 不存在 |
| `napcat/config/*.json` | 7 | 0 |

另外 `qq-turns.jsonl` 改名 `qq-turns.jsonl.pre-dr`——**旧上下文留着但不参与新会话**。

---

## 3. 重装

重启 AstrBot 让它重建空的 data 目录：

```bash
kill -TERM $(sed -n 's/.*"pid": \([0-9]*\).*/\1/p' /manager/astrbot-instance.json)
# App 会停在「AstrBot 异常退出」，在 App 里点一次「启动服务」
# 起来之后 6185 才重新监听
```

再装：

```bash
cd /android-share/dr-repo && bash scripts/deploy-phone.sh
```

**本次输出要点**

- 12 个插件全部就位（`android_observer` 与 `execrelay` 判为「已存在，跳过」，前者是 App 自带的，后者是留给通道的）
- 6 个插件配置全部写入
- `cmd_config.json` 合并前自动备份成 `cmd_config.json.bak-<时间>`，DeepSeek Key / OneBot 6199 / persona_id 都写进去
- 人设：`default` 更新为 2071 字，`dafeiyu` **新增** 1236 字（备份先落 `personas-backup-<时间>.json`）
- NapCat：5 个配置按模板渲染；`onebot11.json` 与 `webui.json` 判为「已存在，保留」（NapCat 启动时自己重建的默认值）

再重启一次让插件加载（同样 kill + 点「启动服务」）。

---

## 4. 验收

```bash
bash scripts/dr-verify.sh
```

**本次结果：16 PASS / 0 FAIL**

```
PASS  AstrBot WebUI 6185 / 反向 WS 6199 监听
PASS  DeepSeek Key 已配置 / OneBot 平台 port=6199
PASS  12 个插件（含 main.py + metadata.yaml）
PASS  人设 default 2071 字 / dafeiyu 1236 字
PASS  NapCat websocketClient -> ws://127.0.0.1:6199/ws
PASS=16 FAIL=0
```

**「不注入旧上下文」的证据**（重装后、发消息前读库）：

```
personas: 2          （default 2071 / dafeiyu 1236）
conversations: 0     ← 旧的两个会话一条都没进来
preferences: 2       （只剩两条自动迁移标记）
umo_aliases: 0
```

**端到端**（主人从大号发一句）：

```
13:09:04  雪见喵（看见提醒我喝水   在吗
13:09:10  雪糕鱼                 在的在的 / 刚睡醒…你是不是也刚起 / 中午了 你吃了吗
```

6 秒、三条短句、不用句号、带自己的生活状态 —— 人设生效。
读库确认 `conversations = 1`，这是**新建的那条**，不是恢复出来的。

**NapCat 没有重新扫码**：反向 WS 是 NapCat 主动连 AstrBot，AstrBot 重启后它自己按
`reconnectInterval: 5000` 连回来，两个方向都是 ESTABLISHED。

---

## 5. 回滚（演练失败时）

```bash
cd /android-share/backup/<TS>
tar xzf astrbot-data.tgz -C /persistent/astrbot
tar xzf napcat-config.tgz -C /persistent/napcat
mv /android-share/qq-turns.jsonl.pre-dr /android-share/qq-turns.jsonl
# App 里点「启动服务」
```

旧上下文要恢复的话，把 `context/` 里的会话正文按 `conversations` 表结构写回去
（`conversation_id / platform_id / user_id / title / persona_id / content / token_usage`），
或者直接把 `astrbot-data.tgz` 里的 `data_v4.db` 换回来——**但那样会连旧会话一起回来**，
和「干净重装」是互斥的两种状态。

---

## 6. 这次演练暴露并修掉的两个坑

| 现象 | 根因 | 修法 |
|---|---|---|
| `verify-phone.sh` 报 `/persistent/astrbot/.venv/bin/python: No such file` | 真机的 python 在 **`/opt/astrbot-venv/bin/python`**，`.venv` 是别的发行版的布局 | 脚本改 `ASTRBOT_PY` 优先 + 两级回退，找不到明确报错（commit `3519677`） |
| `verify-phone.sh` 报「反向 WS 6199 没在监听」，实际在听 | 容器没有 `ss` / `lsof`；`netstat` 在 proot 下读不到 `/proc/net`，解析为空 | 改用 `socket.connect_ex` 打 127.0.0.1（同一 commit） |

第一个坑会让 3 条检查直接报错，第二个会让端口检查恒定误报 FAIL——**没有真机跑一遍是发现不了的**。

---

## 7. 已知限制

- `kill -TERM` 之后 App 不会自动拉起，会停在「AstrBot 异常退出」，**必须人在 App 里点一次「启动服务」**。
  这是 App 的管理策略（`restartPolicy: managed`），不是这套包能改的。
- 本次是**同一台手机**上的删了重装，不是换一台新手机。换机还要多做：装 App、初始化环境、
  扫码登录 QQ。那部分见 `docs/02-phone-quickstart.md`。
- `data_v4.db` 里删掉的不只是会话，还有 `umo_aliases` 与部分偏好——重装后它们会按新对话自然重建，
  不需要手工补。
