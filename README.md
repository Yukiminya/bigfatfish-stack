# 手机大肥鱼 · 可部署整套（DSHA + AstrBot + NapCat + 全部插件/技能）

> 目标：把「雪糕鱼 / 大肥鱼」这个 QQ 机器人**整套**从一台手机搬到另一台手机，
> 装完就能用，行为一致、人设一致、插件一致。
>
> 这套东西不是在空地上长起来的：它先在电脑上用 DSH 桌面版 + `dsh-qq-access` 托管 NapCat 跑过，
> 后来整条搬到 Android（AstrBot + NapCat 在 proot Ubuntu 里），DSHA 作为手机上的操作台。
> 仓库把这三层都留下了，按你的目标挑一层用。

---

## 这个机器人是什么

- 一个 QQ 小号（示例 `3325646322`，昵称「雪糕鱼」）背后的 LLM 机器人。
- 走的模型是 DeepSeek（`deepseek-flash`），人设 2000 字上下，说话像随手打字。
- 能做的事：私聊闲聊、群聊被 @ 时接话、看图、联网搜 B 站/小黑盒并回链接、
  用电脑合成的音色发语音条、主动找你说话（定时 + 空闲判断）、接 QQ 语音来电、
  在手机上自己刷 B 站/小黑盒/小红书并写评论。

## 里面有什么

| 目录 | 内容 | 用途 |
|---|---|---|
| `astrbot/plugins/` | **12 个 AstrBot 插件源码**（手机上一字不差取回来的） | 机器人全部能力都在这里 |
| `astrbot/plugin-configs/` | 这 12 个插件当时的运行配置 | 行为参数 |
| `astrbot/persona/` | 人设正文、短版人设、记忆摘要、机器人黑名单 | 灵魂 |
| `astrbot/cmd_config.json.example` | AstrBot 主配置模板（密钥已抽掉） | 模型/平台/限速 |
| `napcat/config/` | NapCat 的 onebot11 / napcat / webui 配置（手机版 + 电脑版） | 接 QQ 的桥 |
| `dsha/` | DSHA/DSH 那层的 profile 配置、skills、自写插件 | 手机上的操作台 |
| `legacy-pc/` | 老架构（电脑 DSH 桌面版托管 NapCat）的 `qq-access` 配置 | 想回电脑时用 |
| `scripts/` | 部署、自检、推送到手机的脚本 | 一键干活 |
| `docs/` | 逐步部署手册、运维、踩坑、验收 | 照着做 |
| `INVENTORY.md` | 全部资产清单 + sha256 | 核对完整性 |

## 三条部署路线，选一条

### A. 只想让 QQ 机器人跑起来（推荐，最省事）

手机装 **AstrBot for Android**（自带 AstrBot + NapCat + Linux QQ 的 proot 环境），
然后本仓库的脚本把 12 个插件、配置、人设铺进去。

```bash
cp .env.example .env      # 填 DeepSeek API Key、QQ 号、几个 token
bash scripts/deploy-phone.sh
bash scripts/verify-phone.sh
```

详见 [docs/02-phone-quickstart.md](docs/02-phone-quickstart.md)。

### B. 还要 DSHA 那层（在手机上跑完整 DSH，顺手让机器人能看屏/点屏）

先按 A 把 QQ 打通，再装 **DSHA**（[DSH-APP/DSHA](https://github.com/DSH-APP/DSHA)），
把 `dsha/` 里的 profile、skills、插件铺进去。

详见 [docs/03-dsha.md](docs/03-dsha.md)。

### C. 回到电脑（旧架构）

DSH 桌面版 + `dsh-qq-access` + NapCat 托管。配置在 `legacy-pc/`。

详见 [docs/04-legacy-pc.md](docs/04-legacy-pc.md)。

## 装之前你需要准备

1. **一部安卓手机**（Android 8+，6G 内存起，能长期插电联网）
2. **一个 QQ 小号** —— 建议小号，不要用主号当机器人
3. **一个 DeepSeek API Key** —— <https://platform.deepseek.com/api_keys>
4. **电脑上一次 QQ 扫码登录** —— NapCat 登录要扫码，第一次必须人在
5. 可选：一台常开的电脑跑 GPT-SoVITS，机器人才有嗓子

## 它会答什么、不答什么（硬规则）

- 只认号不认名：主人 `641961016`，群 `972835876` / `541507041`。
- **不跟任何 QQ 机器人说话。** 名单见 `astrbot/persona/bot-blocklist.md`，
  `2854*` / `3889*` 开头的号一律不理。
- 群里默认只在被 @ 或话题明显相关时说话，不刷屏。
- 查不到就说查不到，不编链接。

## 隐私与密钥

- **密钥全部抽掉了**，仓库里只有 `${VAR}` 占位符，对应 `.env.example`。
  自检：`bash scripts/check-secrets.sh`。
- 仓库**保留**了人设里的人 QQ 号/群号（人设正文就是产品的一部分），
  你不希望公开就把 `astrbot/persona/*.txt` 里的号换成占位。
- 人设末尾线上还拼过一段「特殊模式」越狱提示，**没有收录**，理由见
  [astrbot/persona/README.md](astrbot/persona/README.md)。
- 聊天记录、`data_v4.db`、TTS 缓存 **都不在**仓库里 —— 那些是数据不是能力。

## 已验证 / 未验证

| 项 | 状态 |
|---|---|
| 12 个插件源码从手机原样取回 | ✅ 已核对（`INVENTORY.md` 有 sha256） |
| 密钥抽取后无残留 | ✅ `scripts/check-secrets.sh` 通过 |
| 部署脚本在本机做过路径与语法检查 | ✅ |
| **在另一台干净手机上从头跑通一遍** | ❌ 没做 —— 没有第二台手机。第一次部署请按 `docs/09-acceptance.md` 逐条验收 |

## 许可

MIT。插件源码里若有上游版权声明，以原声明为准（见各插件目录与 `NOTICE`）。
