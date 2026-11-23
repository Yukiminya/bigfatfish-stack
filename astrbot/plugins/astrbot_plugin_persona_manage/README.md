# 人设提示词管理

## 安装

把三个文件放入 Android 真机的
`/persistent/astrbot/data/plugins/astrbot_plugin_persona_manage/`。
首次安装让 AstrBot 发现并启用插件。更新已加载插件时，在后台重载，
或向已鉴权的 `POST /api/plugin/reload` 提交
`{"name":"astrbot_plugin_persona_manage"}`。

先登录管理后台，再用同一浏览器、同一协议和主机打开
`/api/plug/astrbot_plugin_persona_manage/panel`。浏览器面板依赖登录 Cookie。
官方 v4.29.3 默认设置 Secure Cookie，普通局域网 HTTP 可能返回 401，
可通过后台的 HTTPS 地址访问。程序调用可使用 `Authorization: Bearer <后台 JWT>`。

## 使用

面板顶部显示全局默认人设及其数据库修改时间。会话、配置档和外部
runner 可以有自己的选择。编辑器随所选人设切换，历史显示最近 20 条。
聊天未指定 ID 时使用全局默认（通常为 `default`）。

- `/人设 查看 [id]`
- `/人设 设置 <新文本>`
- `/人设 追加 <追加文本>`，用换行连接新旧文本
- `/人设 历史 [id]`
- `/人设 回滚 [id] <ts>`，复制历史列表中的完整时间戳

写命令要求 AstrBot 管理员（`admins_id` 对应的 `event.is_admin()`）。
聊天查看和历史对普通用户开放。查看回复的最大可显示长度由消息平台决定。

| 方法 | 相对路径 | 参数 |
| --- | --- | --- |
| GET | `/state` | 可选查询参数 `persona_id` |
| GET | `/history` | 可选查询参数 `persona_id`，返回最多 50 条 |
| POST | `/save` | JSON `persona_id`、`system_prompt` |
| POST | `/rollback` | JSON `persona_id`、`ts` |

以上路径都以 `/api/plug/astrbot_plugin_persona_manage` 为前缀。
写入上限为 UTF-8 204800 字节，超限返回 413，空白或无效输入返回 400。
这些规则也适用于回滚目标。时间戳使用 UTC，面板用浏览器本地时区显示。
每次写入前的旧文本保存在插件数据目录的 `history/<persona_id>/`，
保留最近 50 份。不能安全用作目录名的 ID 映射到 `~<sha256>`。
插件内的 Web 和聊天写入串行执行。原生后台和其他插件可独立写入同一人设，
这些写入不进入本插件的历史。新请求会使用刷新的运行时人设。

## 实现与验证

v4.29.3 的 `Context.persona_manager` 提供 `get_all_personas()`、
`get_persona()` 和 `update_persona()`。常规写入只传 `system_prompt`，
底层事务自动提交，Manager 随后重建缓存。若更新中途失败，插件恢复
进程内缓存，并通过内置 SQLite helper 补偿已经提交的提示词写入。
若数据库同时不可写，旧文本仍保存在快照中。

`Context.register_web_api(route, view_handler, methods, desc)` 注册异步路由。
`/api/plug/...` 经后台鉴权，视图从 `astrbot.api.web.request.username`
读取已验证的用户名。源码参考 [PersonaManager](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/persona_mgr.py)、
[Context](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/star/context.py)、
[插件路由](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/dashboard/api/plugins.py) 和
[管理员角色赋值](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/pipeline/waking_check/stage.py)。

实际执行的命令（Linux，Python 3.12.14）及结果：

~~~sh
git clone --depth 1 --branch v4.29.3 https://github.com/AstrBotDevs/AstrBot.git work/AstrBot
python -m venv work/venv
work/venv/bin/pip install -e work/AstrBot
work/venv/bin/pip show astrbot
export ASTRBOT_ROOT=/workspace/work/runtime PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/workspace/work/plugin
work/venv/bin/python -c "import main"
work/venv/bin/python work/smoke.py
node --check work/panel.js
~~~

安装和导入通过，pip 显示 4.29.3，提取的面板 JavaScript 通过语法检查。
临时 smoke 脚本使用官方 Dashboard ASGI 应用、真实 SQLite 和 PersonaManager。
验证了无 Cookie 401、有 Cookie 200 HTML、400/413、保存后官方 GET 返回新文本、
运行时缓存一致、回滚保留 CRLF、模拟缓存重建失败后恢复旧值、保留 50 份快照。
聊天测试直接调用处理器，用替身事件验证普通用户拒写和管理员多行追加。
Android 真机加载、热重载和手机浏览器交互留给真机验证。
