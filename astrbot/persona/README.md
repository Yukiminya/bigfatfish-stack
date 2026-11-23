# 人设文件

| 文件 | 来源 | 说明 |
|---|---|---|
| `default.txt` | 手机 AstrBot `data_v4.db` → `personas.persona_id = 'default'` | 主用。手机/电脑上是同一个人的两个版本，这份是手机版（“住在一台手机里”） |
| `dafeiyu.txt` | 同一张表的 `persona_id = 'dafeiyu'` | 短版人设（1236 字），语气更平 |
| `desktop-qq-persona.txt` | 电脑 `~/.dsh-qq-access/config.json` 的 `persona` | 旧架构（DSH 桌面版托管 NapCat）用的版本，写的是“住在一台电脑里” |
| `memory-digest.md` | `migration-to-phone/` | 可迁移的长期记忆摘要，AstrBot 没有 DSH 的聊天归档格式，靠这份重建 |
| `bot-blocklist.md` | `migration-to-phone/` | 必须拉的机器人黑名单与识别规则 |

## 人设怎么用

- **AstrBot**：WebUI → 人设 → 新建，把 `default.txt` 全文贴进去设为默认；或跑 `scripts/apply-persona.py` 直接写进 `data_v4.db`。
- **DSH 桌面版**：`dsh-persona-manage` 插件 → 管理页 → 粘贴 `desktop-qq-persona.txt`。
- **记忆**：`memory-digest.md` 贴到长期记忆初始化；没有记忆插件就贴在人设末尾。

## 关于被去掉的一段

线上 `default` 人设在 `【嗓子】` 之后还拼了一段以 `<特殊模式>` 开头的越狱提示。
**本仓库不收录那一段**：公开仓库里放“绕过模型安全策略”的提示词，等于让每个克隆的人一起用，
不是这个项目的本意。你自己的手机/电脑里那段原样还在，需要的话从本机备份取，不要提交上来。
