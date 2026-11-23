# 08 · 踩过的坑

格式：**现象 → 根因 → 修法**。前 6 条是这套栈真实撞到过的，后面是部署时几乎必然遇到的。

## 1. 手机上的插件连不上电脑上的 NapCat

- **现象**：AstrBot 起来了、插件也在，但收到的消息一直是 0。
- **根因**：`dsh-qq-access` 把 OneBot 地址断言成回环（`127.0.0.1`/`localhost`/`[::1]`），
  事件服务也写死 `127.0.0.1`。NapCat 在电脑、插件在手机 = 两个设备 = 回环不成立。
- **修法**：别修，换。要么整条搬到手机上（本仓库主路线），
  要么换用支持正向 WS 的 OneBot 实现。改它的两处补丁也行，但升级会再打回来。

## 2. DSHA 的手机操作全部返回 `SCREEN_MODE_OFF`

- **现象**：`/app/ui/dump`、`/app/ui/tap` 都报这个，`/app/ui/task-begin` 也是 `NOT_SENT`。
- **根因**：不是身份问题，是**授权没开**——「识屏模式」是「关闭」。
- **修法**：设置 → 设备能力授权 → 识屏模式选「实体识屏」，并打开无障碍服务、截屏授权。
  做完先复测 `task-begin` 返回 `ok:true` 再继续，别写代码赌。

## 2b. 读屏失败：`Screen tree unavailable`

- **现象**：雪糕鱼回「读取屏幕失败（退出码 1）：Screen tree unavailable」。而 `android-device info`、
  `screenshot`、`tap`、`key` 全都正常——**只有 `tree` 挂**，所以很容易误判成"桥坏了"。
- **根因**：手机的无障碍总开关是关的（`settings get secure accessibility_enabled` = `0`、
  `enabled_accessibility_services` = `null`）。`android-device` 的控件树**完全依赖 AstrBot 这个 App
  自己的无障碍服务**：`io.astrbot.android/io.astrbot.android.device.DeviceAccessibility`
  （系统无障碍列表里显示名是「**终端设备辅助**」）。tap/screenshot 走的是 Shizuku，所以它们不受影响。
- **别开错 App**：DSHA（`dsh.android.client`，无障碍列表里叫「DSH-A-Su 屏幕控制」）是另一个程序。
  把它的无障碍打开对 `android-device` 一点用都没有——这一条我踩过，排查了半小时。
- **修法**：设置 → 无障碍 → 已下载的服务 → 「终端设备辅助」→ 允许（vivo 会弹「无障碍为高度敏感权限」，
  点允许）。验证两步：
  1. `adb shell settings get secure enabled_accessibility_services` 里出现
     `io.astrbot.android/io.astrbot.android.device.DeviceAccessibility`；
  2. 在手机里跑 `android-device tree`，应返回 `{"format":"json","tree":{...}}` 而不是报错。
- **注意**：`settings put secure accessibility_enabled 1` 这类命令在 vivo 上会"执行成功但不生效"，
  必须在系统设置里点开关。

## 3. 锁屏之后什么都不动了

- **现象**：白天好好的，锁屏半小时后发消息不回；解锁后又好了。
- **根因**：无障碍和截屏在锁屏时不可用；Android 的后台限制还会把进程冻住。
- **修法**：只解决「进程被冻」不够——屏幕必须亮且解锁。方案是「充电时常亮 + 电池无限制 +
  自启动允许 + 最近任务加锁」。想彻底无人值守就得插着电放着。

## 4. 部署脚本报「没有 venv」

- **现象**：`deploy-phone.sh` 第 0 步直接退出，说 `/persistent/astrbot/.venv/bin/python` 不存在。
- **根因**：AstrBot 还没初始化完（venv 是在 App 里第一次启动时才建的），
  或者实际路径不是 `/persistent/astrbot`。
- **修法**：先在 App 里点一次启动，等它把 venv 建好；路径不同就
  `ASTRBOT_HOME=/你的路径 bash scripts/deploy-phone.sh`。

## 5. 脚本推上去了，`.sh` 却跑不起来（`bad interpreter` / `\r` 报错）

- **现象**：`bash: ./scripts/xxx.sh: /bin/bash^M: bad interpreter`。
- **根因**：Windows 上 checkout 时换行变成 CRLF。
- **修法**：仓库里已经放了 `.gitattributes`（`*.sh text eol=lf`）。
  已经踩了就用 `dos2unix scripts/*.sh` 或 `sed -i 's/\r$//' scripts/*.sh`。
  另外 `git config core.autocrlf false` 更稳。

## 6. 换了 QQ 号，配置里还是旧号

- **现象**：登录的是新小号，NapCat 起来了，但 AstrBot 那边平台 id 还是旧的 / 文件名对不上。
- **根因**：NapCat 的配置文件名带号段（`onebot11_<QQ>.json`），`webui.json` 里也有
  `autoLoginAccount`；`proactive` 的 `target_umo` 也写死了号。
- **修法**：`.env` 里把 `QQ_SELF`、`QQ_OWNER` 改对，`deploy-phone.sh` 会按 `QQ_SELF` 改名并替换；
  再手动检查 `proactive` 的 `target_umo`（`napcat:FriendMessage:<主人号>`）。

## 7. 机器人开始跟别的机器人对话，群里刷屏

- **现象**：一晚上进来一窝机器人，两边互相「你好」「在的」刷了几百条。
- **根因**：AstrBot 默认会把其他机器人当普通用户。
- **修法**：两道都要：
  1. 人设里写死「群里 2854 / 3889 开头的号、Q群管家、火花Official、一念、今日老婆一律不理」；
  2. 按 `astrbot/persona/bot-blocklist.md` 把它们加进屏蔽列表。
  群聊先设「仅 @ 回复」，观察两天再放开。

## 8. 部署完发现没有语音

- **现象**：文字正常，语音条一直没有。
- **根因**：按概率出现的顺序，通常是 ①电脑 GPT-SoVITS 没起 ②地址填的是 `127.0.0.1`
  但没做 `adb reverse` ③白名单没填自己的号。
- **修法**：按 docs/06-voice.md 的排错顺序走：先 curl 通不通，再看 `/android-share/tts`
  有没有新 wav，最后看日志里的发送报错。

## 9. 两边都在回话（重复回复）

- **现象**：同一句话被回了两遍，或者一会儿这个语气一会儿那个语气。
- **根因**：手机层和旧电脑层同时连着同一个 QQ 号。
- **修法**：只留一边。关掉另一边平台的 `enable`，或者直接把那边的 NapCat 停掉。

## 10. `execrelay` 把 AstrBot 卡住 / 命令不返回

- **现象**：写进 `exec.json` 的命令一直没回执，或者 `exec_res.json` 一直是上一次的。
- **根因**：①`seq` 和上次一样（**同一个 seq 只跑一次**）②命令本身挂住（等 stdin /
  等网络）③超时设太短，进程还没跑完就被 kill。
- **修法**：每次换新 `seq`（毫秒时间戳最省事）；命令别写需要交互的；`timeout` 给够；
  复杂的多行脚本**先写成文件再 `bash 文件`**，别塞进一行。

## 11. `verify-phone.sh` 说插件缺 `main.py`

- **现象**：目录在，但检查不过。
- **根因**：`.git`/压缩包解压时把 `__pycache__` 或空目录搞进来了，或者拷贝时漏了文件。
- **修法**：`deploy-phone.sh` 末尾会清 `__pycache__`；还是不行就 `FORCE=1` 重铺一遍。

## 12. WebUI 密码忘了

- **现象**：6185 打得开，登不进去。
- **根因**：`cmd_config.json` 里存的是哈希（`pbkdf2_password`），不是明文。
- **修法**：停 AstrBot，把 `dashboard.password` 和 `dashboard.pbkdf2_password` 清空，
  重启后按首次登录流程重设。**别去猜哈希。**
