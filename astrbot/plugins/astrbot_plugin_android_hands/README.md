# 雪糕鱼的手机（astrbot_plugin_android_hands）

让 QQ 里的机器人操作这台 Android 手机：读屏、点击、滑动、按键、输入、打开应用，**自己点进视频看**，并且**闲着的时候自己找视频看**。

完全在手机上运行，不需要电脑、不需要 adb、不需要任何外部服务。

## 原理

AstrBot for Android 自带设备桥命令 `android-device`（PATH 里），背后是本 App 的无障碍服务：

```
QQ → NapCat → AstrBot(插件) → android-device → 无障碍服务 → 手机屏幕
```

## 前提（在 App 里打开一次）

设置 → 权限 → Android 设备访问：打开「Android 设备访问」「允许读屏」「允许点击和输入」，
「终端设备辅助」显示「已连接」。

## 指令

| 指令 | 作用 |
|---|---|
| `/手机` | 机型、系统、分辨率、锁屏、AstrBot 与 NapCat 状态 |
| `/看视频 [条数]` | **点进** B 站视频，停留一会儿再退出，回复看了什么 |
| `/自动看视频 on` | 开启自主模式：以后自己找视频看，感想发到这个会话 |
| `/自动看视频 off` / `status` | 关掉 / 查看当前设置 |
| `/刷视频 [次数]` | 只在信息流里上滑，不点进去（老功能） |
| `/截图` | 把手机当前画面截下来发到会话 |
| `/溜达 [应用] [评论/只看]` | 现在就去 B 站 / 小黑盒 / 小红书逛一条，读内容、写评论，有趣的连截图发给你 |
| `/自动溜达 on` / `off` / `status` | 定时自己出去逛，默认 25-60 分钟一条 |

## 工具（模型自动调用）

`phone_screen` `phone_tap` `phone_tap_xy` `phone_swipe` `phone_key`
`phone_input` `phone_launch` `phone_status` `phone_watch_video` `phone_shot` `phone_roam`

## 自动溜达怎么工作

`hands_roam.py` 里的 `Roamer`：

1. 打开应用（桌面图标 → 最近任务 → 应用抽屉逐屏找，按包名确认真的打开了）
2. 回到信息流，挑一条没逛过的内容点进去
3. 读屏拿标题和正文，交给模型写一句 20-40 字的中文评论
4. 找评论输入框 → 输入 → 发送，并用「评论数变化 / 自己的评论出现」验证
5. 模型觉得有趣就连截图一起发到会话
6. 记进 `plugin_data/<插件>/roam_state.json`，同一条不会重复逛

### 截图要 Shizuku

`phone_shot` 走 `android-device screenshot`，需要 Shizuku 在运行并且已授权：

- 手机装 Shizuku（RikkaApps/Shizuku），用 adb 或无线调试把服务启动起来
- AstrBot → 设置 → 权限 →「Shizuku 已运行，等待授权」→ **授权连接** → 始终允许

授权一次就记住了。**手机重启后 Shizuku 会停**，截图会报错提示重新授权，
其余功能（读屏、点击、输入、看视频）不受影响。

## 自主模式怎么工作

`hands_watch.py` 里的 `AutoWatcher`：

1. 首轮等 30 秒，之后每 `auto_min_gap`~`auto_max_gap` 分钟醒一次
2. 只在 `auto_hour_start`~`auto_hour_end` 点之间活动（支持跨零点）
3. 每天最多 `auto_daily_limit` 条，每轮有 `auto_skip_probability` 概率直接跳过
4. 从信息流挑一条没看过的视频点进去，停留 `watch_min`~`watch_max` 秒
5. 读标题 / UP 主 / 播放量 / 播放器上的文字，让模型写一段第一人称感想
6. 用 `context.send_message(umo, ...)` 发到开启时所在的那个会话
7. 状态存 `data/plugin_data/astrbot_plugin_android_hands/auto_state.json`，
   开关存 `auto_ctl.json`，重启后自动恢复

## 看懂视频靠什么

`bili_api.py` 按标题把视频找回来（多路搜索 + 相似度），然后一次取齐：

| 素材 | 来源 | 要不要登录 |
|---|---|---|
| 封面 + 4 张关键帧 | `/x/player/videoshot` 关键帧拼图，PIL 裁 | 不要 |
| 台词（AI 字幕） | `/x/player/wbi/v2` → `subtitle_url`，拼句后均匀取 45 行 | **要** |
| 弹幕 | `/x/v1/dm/list.so`，均匀抽 40 条带时间戳 | 不要 |
| 热评 | `/x/v2/reply` 按热度前 6 条 | 不要 |

图片交给能看图的 `deepseek-flash`，台词 / 弹幕 / 热评作为文字一起给，
所以感想能同时引用画面细节、UP 主原话和弹幕吐槽。

### 字幕需要一次扫码登录

`SESSDATA` 存在手机上（`plugin_data/<插件>/bili_cookie.json`），
第一次可以从 `/android-share/bili_cookie.json` 拷过来。没有登录态时字幕静默跳过，其余功能照常。

两个坑（已在代码里处理）：

- `/x/player/v2` 不签名会**随手返回别的视频的字幕**，必须走 `/x/player/wbi/v2` 带 wbi 签名
- 即使签名了偶尔也会错，所以用「字幕结束时间 vs 视频时长」比对，对不上就丢掉重试

## 已知限制

- 截图需要 Shizuku（见上），读屏和点击不需要
- 锁屏时读屏会暂停，需要先解锁
- 打开应用靠桌面图标文字；B 站会恢复上次停留的页面，插件会自动退回信息流继续
- 广告卡片（`广告,...`）会被跳过
- 没有 AI 字幕的视频（纯音乐、无解说）就拿不到台词，只靠画面和弹幕
