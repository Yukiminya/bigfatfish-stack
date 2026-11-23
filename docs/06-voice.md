# 06 · 嗓子：语音条与语音来电

雪糕鱼的「嗓子」不在手机上，在**电脑上的 GPT-SoVITS**。手机只负责把文字发过去、把 wav 收回来、
塞进 QQ 的语音消息。

## 链路全景

```
AstrBot 生成回复
   ├─ feiyu_voice（每条回复）
   │    remote_urls 依次试：127.0.0.1:9880 → 192.168.x.x:9881 → edge:<voice>
   │    拿到 wav → 存 /android-share/tts → 作为 QQ 语音消息发出（只发给白名单）
   │
   ├─ proactive（主动说话时）
   │    自己带一套 remote_tts_urls，失败退回 voice_local.py（手机本地 VITS）
   │
   └─ qq_voice_call（有人打语音电话进来）
        音频进来 → 本地 ASR（/android-share/asr/*.onnx）→ 大模型 → GPT-SoVITS → 音频回去
```

## 第 1 步：在电脑上起 GPT-SoVITS

目标是两个东西：**HTTP 接口**和**你的音色模型**。

1. 电脑上部署 GPT-SoVITS（官方仓库或整合包都行），加载你的音色模型。
2. 开 api 服务，默认监听 `9880`。
3. 自测：`curl -s -o test.wav http://127.0.0.1:9880/...`（按你那份 api 的接口签名），
   能拿到 wav 就算成。
4. 想让手机连到电脑，有两条路：
   - **同 Wi-Fi + 局域网 IP**：电脑防火墙放行 9880，手机把 `TTS_URLS` 写成 `http://<电脑IP>:9880`
   - **adb 隧道**：`adb reverse tcp:9880 tcp:9880`，手机上就变成 `http://127.0.0.1:9880`

> 现有配置里同时写了 `127.0.0.1:9880` 和 `192.168.1.7:9881`，就是「隧道 + 局域网备用」两条路。

## 第 2 步：把地址填进配置

`.env` 里：

```bash
TTS_URLS=http://127.0.0.1:9880,http://192.168.1.7:9881,edge:zh-CN-XiaoyiNeural
```

会分别写进：

- `astrbot/plugin-configs/astrbot_plugin_feiyu_voice_config.json` 的 `remote_urls`
- `astrbot/plugin-configs/astrbot_plugin_proactive_config.json` 的 `remote_tts_urls`

三段的语义：**第一个通就用第一个**；前两个都是 GPT-SoVITS；最后的 `edge:<voice>` 是兜底
（微软 edge-tts，音色不是你的，但至少有声）。

## 第 3 步：白名单

```json
"voice_whitelist": "641961016"
```

只有这个号在私聊里能收到语音条。群里默认不发——这是故意的：语音在群里刷屏很烦，
而且会暴露音色。

## 第 4 步：验

1. **通不通**：跑 `probe` 插件，或手机上
   `curl -sS -m 5 -o /tmp/t.wav -w '%{http_code}' <TTS_URL>`，看 `200` 且文件是 RIFF 头。
2. **有没有生成**：`ls -lt /android-share/tts | head`，刚发完消息应该多一个 wav。
3. **发没发出去**：私聊发一句，等语音条；同时看 AstrBot 日志有没有 `send_record` 相关的报错。

## 排错顺序（记住这个顺序，别乱试）

| 现象 | 先查 | 再查 |
|---|---|---|
| 完全没声音 | 9880 通不通（curl） | 白名单号对不对 |
| 有 wav 但 QQ 里没收到 | NapCat 能不能发语音（协议支持） | AstrBot 日志里的发送报错 |
| 声音不对（不是你的音色） | 是不是落到了 `edge:` 兜底 | GPT-SoVITS 的参考音频/模型有没有加载对 |
| 时有时无 | GPT-SoVITS 是不是在重载模型 | 手机和电脑的 Wi-Fi 是不是掉到别的网段了 |
| 语音条很长 | `max_chars`（feiyu_voice 120 / proactive 60 / 来电 60） | `speed` |

## 语音来电（qq_voice_call）额外的东西

- NapCat 侧要有一条**音频通路**：`speaker_monitor: qqspk.monitor`、`mic_sink: qqmic`，
  本质是把 QQ 的通话音频桥到容器的虚拟设备上。
- ASR 模型放 `/android-share/asr/`：`model.int8.onnx` + `tokens.txt`。
- 日志在 `/android-share/qqcall.log`，调试细节在 `qqcall_probe.log`。
- **建议把 `voice_uid_whitelist` 填上主人**，否则任何人都能打进来跟你的鱼通话，烧的是你的 token。

## 手机本地兜底（没电脑时）

`astrbot_plugin_proactive/voice_local.py` 是手机本地 VITS 的兜底路径，音质和音色都比远程差。
没有电脑、又非要语音，才用它；有电脑就用远程。

> 未验证：本地 VITS 在目标机型上的合成耗时（与 CPU 有关），没在第二台手机上量过。
