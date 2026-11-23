# 09 · 验收清单

部署完**逐条勾**。每一条都写清「怎么验、期望看到什么」。有一条不过就别往下走。

## A. 服务层

- [ ] **AstrBot WebUI 通**
  ```bash
  adb forward tcp:16185 tcp:6185
  curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:16185/
  ```
  期望 `200`（或 `302` 到登录页）。

- [ ] **反向 WS 在听**
  ```bash
  bash scripts/verify-phone.sh
  ```
  期望 `PASS  反向 WS 服务在 6199 监听`。

- [ ] **NapCat 作为 client 连上了**
  期望 `PASS  有 websocketClient -> ws://127.0.0.1:6199/ws`。

- [ ] **模型可用**
  期望 `PASS  DeepSeek API Key 已配置`；WebUI 里点一次「获取模型」，能列出 `deepseek-flash`。

- [ ] **12 个插件都在**
  期望 12 条 `PASS 插件 astrbot_plugin_*`，没有 FAIL。

## B. 人设层

- [ ] **人设写进去了**
  期望 `PASS  人设 default 长度 <非 0>`。

- [ ] **用的是对的那个人设**
  `data/cmd_config.json` → `agent_runner.config.persona.persona_id` 是 `default`。

- [ ] **机器人黑名单在**
  按 `astrbot/persona/bot-blocklist.md` 核对，`2854*` / `3889*` 开头的号都在屏蔽列表里。

## C. 对话层（人真的在 QQ 上试）

- [ ] **私聊能回**
  大号给小号发「在吗」。期望：几秒内回一条**短句**（不超过 30 字、不带句号、不像客服）。

- [ ] **群里 @ 能回**
  在常驻群里 @ 小号问一句。期望：回一句话，不刷屏。

- [ ] **不跟机器人说话**
  让一个已知机器人号（或 `2854*` 开头的号）发一句。期望：**完全不回**。

- [ ] **看图**
  发一张图并问「这上面写的啥」。期望：能答对内容；看不到就老实说看不到。

- [ ] **分条像人**
  问一个需要多句回的问题。期望：拆成 2~3 条短消息，不是一大段。

## D. 嗓子（有 GPT-SoVITS 才勾）

- [ ] **TTS 通**
  ```bash
  curl -sS -m 5 -o /tmp/t.wav -w '%{http_code}\n' "$TTS_URL"
  ```
  期望 `200`，且 `/tmp/t.wav` 开头是 `RIFF`。

- [ ] **生成了 wav**
  发一条私聊后 `ls -lt /android-share/tts | head`，期望出现新的 wav。

- [ ] **QQ 里收到语音条**
  期望每条回复后面跟几秒语音，音色是训练的那个。

- [ ] **群里不发语音**
  群里 @ 它，期望只有文字。

## E. 手机自动化（有 DSHA 才勾）

- [ ] **桥通**
  ```bash
  adb forward tcp:23091 tcp:3091
  curl -H "X-Token: <token>" http://127.0.0.1:23091/app/version
  ```
  期望返回 `BRIDGE_PROTOCOL=3`。

- [ ] **识屏开着**
  `/app/ui/task-begin?action=begin&taskSessionId=probe&turnId=1` 返回 `ok:true`
  （返回 `SCREEN_MODE_OFF` 就是授权没开，去设置里开，别绕过）。

- [ ] **能读屏**
  `/app/ui/dump` 有内容，不是空树。

- [ ] **能点/能滑**
  让它打开 B 站并滑两下，屏幕上真的动了。

- [ ] **QQ 里下指令能落到手机上**
  私聊发「打开 B 站」，手机上真的打开。
  ⚠ 这条是三者联合条件（门禁 + 控制插件 + 人设），历史上**未验证**过 QQ 触发的轮次能不能过门禁。

## F. 保活（最容易被忽略）

- [ ] **锁屏 30 分钟后还能回**
  锁屏、等 30 分钟、用大号发一句。期望：正常回复。
  不回复 → 回去做 docs/02 的第 2 步（电池无限制 / 自启动 / 加锁 / 充电常亮）。

- [ ] **重启手机后自动起来**
  重启，等 3 分钟，发一句测试。期望：不用手动点 App 也能回。

- [ ] **断网恢复后能自己连回来**
  关 Wi-Fi 10 秒再开。期望：NapCat 自己重连（`reconnectInterval: 5000`），不用重启。

## G. 收尾

- [ ] **`execrelay` 已停用**（调试完了就关，它是文件触发的命令执行）
- [ ] **`voice_uid_whitelist` 已填主人**（不然谁都能打语音进来）
- [ ] **WebUI 密码不是默认值**，6185 只绑 127.0.0.1
- [ ] **备份做了一次**，`data_v4.db` 和 `data/config/` 都拉到了电脑上
- [ ] **旧电脑层已断开**（如果之前开着），确认没有两边同时回话

---

## 出问题时最省时间的三条命令

```bash
bash scripts/verify-phone.sh                                  # 服务/插件/人设/NapCat 一次看全
tail -n 100 /persistent/astrbot/data/logs/astrbot.log         # 打开落盘后才有
adb logcat -d | grep -i astrbot | tail -n 50                   # 连 App 层都看
```
