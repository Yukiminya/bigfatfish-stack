# 任务：给 AstrBot 插件加「高亮热区识别」+ 出一版游戏数据分析脚本

只输出代码，不用问我背景。目标插件：AstrBot 4.29.3 的 `astrbot_plugin_android_hands`，跑在手机 proot Ubuntu 里，Python 3.11，已装 aiohttp / Pillow / numpy。

## 现状（这些已经存在，不要重写）

手机设备桥是 PATH 里的 `android-device`，子命令：`info`、`tree`、`tap x y`、`swipe x1 y1 x2 y2 --duration 毫秒`、`input 文字`、`key 名字`、`screenshot 绝对路径`。

插件里已有的关键部件（照这个风格写）：

```python
_COMMANDS = {"info": "读取手机信息", "app-status": "读取服务状态", "tree": "读取屏幕",
             "screenshot": "截取屏幕", "tap": "点击屏幕", "swipe": "滑动屏幕",
             "input": "输入文字", "key": "发送按键"}
_DEVICE_LOCK = asyncio.Lock()

async def _device(self, command: str, *args: str, expect_json: bool = False) -> dict
async def _capture(self, path: str | None = None) -> str        # 截图，默认存 data_dir/screen.png
async def _display_size(self) -> tuple[int, int]                # 设备像素，来自 info，缓存
async def _serialized(self, operation, *args)                   # async with _DEVICE_LOCK
async def _tool_text(self, operation, *args, serialize: bool = True) -> str   # 捕获 PhoneError 转中文文本
async def _tap_point(self, x: int, y: int) -> None
async def _look(self, question: str = "") -> str                # 已有的看图工具，锁外调视觉模型

@filter.llm_tool(name="phone_look")
async def phone_look(self, event: AstrMessageEvent, question: str = "") -> MessageEventResult:
    """读屏没有文字（游戏、画布界面）时用。会截图交给视觉模型，返回画面描述和可点位置的像素坐标。

    Args:
        question(string): 可选，想问画面的问题，比如「开始按钮在哪」
    """
    yield await self._tool_text(self._look, question, serialize=False)
```

配置一律 `self._config.get(...)` 读；`_conf_schema.json` 最后一项现在是 `vision_timeout`。插件里同目录模块用**裸导入**加 `# noqa: E402`（因为 main.py 会把自身目录插进 sys.path），例如 `from vision_look import VisionClient, VisionError, to_pixels  # noqa: E402`。

## 背景：为什么要做「热区识别」

目标游戏（LostLife，跑在 `SurfaceView` 上）**点右上角的问号会把所有可交互区域用青色半透明块高亮出来**。也就是说"哪里能点/能拖"这件事游戏自己会告诉我们，不用视觉模型去猜。

我实测了高亮色（从玩家截图里采样，1080×2400 的截图）：高亮块是 **R≈0、G≈72~104、B≈72~104 的青绿色**，最密的是 `(0,80,80)`、`(0,88,80)`、`(0,88,88)`。所以判据大致是 `R < 45 且 55 <= G <= 125 且 55 <= B <= 125 且 |G-B| <= 30`，比视觉模型猜坐标靠谱得多，而且免费、可重复。

## 要交付的四份东西

### 1) 新文件 `hotspots.py`（与 main.py 同目录，**不 import astrbot**）

只用标准库 + numpy + Pillow，要能离线单测：

- `find_hotspots(image_path: str, tolerance: int = 45, min_area: int = 400) -> list[dict]`
  - 按上面的判据生成布尔掩码（`tolerance` 控制 R 的上限容差，别写死 45，做成参数）
  - **自己做连通域标记**（不要依赖 scipy / OpenCV）：用行扫描 + 并查集，或 BFS 队列都行；一张 1080×2400 的图要在 1 秒内跑完
  - 把同一热区里被前景切碎的碎块合并：两个框的**间距小于 12 像素**就并成一个（游戏热区常常中间被图标切成几块）
  - 丢掉面积小于 `min_area` 像素的碎点，丢掉宽或高小于 8 像素的细条
  - 输出按面积从大到小排序，每项：`{"x": 左上x, "y": 左上y, "w": 宽, "h": 高, "cx": 中心x, "cy": 中心y}`，都是**上传图的整像素**
- `to_screen(boxes, image_size, screen_size) -> list[dict]`
  - 把上传图上的框换算到设备分辨率（截图会被缩放，比如 1080×2400 的设备截出来是 486×1080）
  - 返回 `{"x","y","w","h","cx","cy"}`，全部夹在屏幕范围内
- `merge_boxes(boxes, gap: int = 12) -> list[dict]` 和 `_components(mask) -> list[tuple]` 单独拆成函数，方便测试

### 2) `prepare_patch_v2.py`（和上一版 `prepare_patch.py` 一样的思路：读真实文件、做 AST 锚点校验、输出补丁）

上一版脚本我已经用过，它的思路很对：`ast.parse` 找类 → 校验锚点唯一 → 生成 unified diff。这次要做的是：

- 读插件目录里的 `main.py`：
  - 顶部导入区（`from vision_look import ...  # noqa: E402` 之后）加 `from hotspots import find_hotspots, to_screen  # noqa: E402`
  - 类里加方法 `_hotspots`（放在 `_look` 方法之后）：截图（**持锁**）→ `find_hotspots` 跑在 `asyncio.to_thread` 里 → `to_screen` 换算 → 返回中文文本，格式仿现有 `_screen()`：
    ```
    当前应用：air.LostLife（热区）
    1. (305,1368) 区域 240x160 | 可点击
    2. (540,1145) 区域 900x420 | 可拖动
    提示：坐标可直接用于 phone_tap_xy、phone_drag、phone_press
    ```
    （宽高换算后四舍五入；一个都没有时返回 `没看到高亮热区：先把游戏里的「?」打开再试`）
  - 加工具 `phone_hotspots(self, event)`，docstring 写清「当画面是可点/可拖、但读屏没有文字，并且游戏能用?号显示高亮热区时用；返回每个热区的编号、像素坐标、宽高和可操作提示」
- 读 `_conf_schema.json`：在 `vision_timeout` 之后追加
  - `hotspot_tolerance` int 默认 45，描述「热区识别：R 通道容差，青绿色高亮块用，游戏高亮色偏深就调大」
  - `hotspot_min_area` int 默认 400，描述「热区识别：小于这么多像素的碎块丢掉」
- 读 `README.md`：末尾追加一小节讲 `phone_hotspots` 怎么用、为什么要先开「?」、两个配置项
- 输出到指定目录：`main.py.patch`、`_conf_schema.json`、`README.md`
- 所有锚点找不到就 `parser.exit(1, "未找到唯一代码锚点 ...")`，**不要猜**

### 3) `scripts/smoke_hotspots.py`（离线单测，纯标准库 + numpy + Pillow）

- 现场用 Pillow 生成测试图（不要依赖外部素材）：
  - 灰底 `(60,60,60)` 上画两个青色块 `(0,85,85)`，中间故意留 6 像素缝，断言 `merge_boxes` 把它们并成一个
  - 画一个 20×20 的小点，断言被 `min_area` 丢掉
  - 画一个纯灰色图，断言返回 `[]`
  - 断言 `to_screen` 把 486×1080 图上的框正确放大到 1080×2400（误差 ≤1 像素）
- 用 `importlib.util.spec_from_file_location` 从 `../astrbot/plugins/astrbot_plugin_android_hands/hotspots.py` 加载
- 打印 `OK` 和通过条数，失败退出码 1

### 4) `scripts/apk_scan.py`（摸清游戏自己的数据，纯标准库）

- 用法：`python scripts/apk_scan.py 游戏.apk --grep 关键词1,关键词2 --out 报告.txt`
- 做三件事，输出中文报告：
  1. 列出 apk 里的 `assets/`、`lib/arm64-v8a/`、`res/raw/` 内容（名字 + 大小，按大小降序，只列前 80 个）
  2. 对 `assets/` 下的每个文件：如果是文本（`.json/.txt/.xml/.csv/.lua/.js/.ini/.yaml`）直接读；如果是二进制，抽取长度 ≥6 的可打印 ASCII 串
  3. 在抽出来的文本里搜 `--grep` 给的关键词（不区分大小写），命中就打印 `文件:行号: 上下文`
- 另外单独报告：apk 里出现的脚本/引擎线索——扫 `assets/` 与 `lib/` 里的文件名和字符串，命中 `unity|il2cpp|cocos|lua|mono|air|gamemaker|renpy|cordova|flutter` 就列出来，帮我判断这游戏是什么引擎做的
- 不要真跑游戏，也不改 apk，只读

## 验收

你拿不到真机和游戏包，所以：

- 对 `hotspots.py`、`scripts/smoke_hotspots.py`、`scripts/apk_scan.py` 做 `python -m py_compile`
- 真实运行 `python scripts/smoke_hotspots.py`，把输出贴出来
- `prepare_patch_v2.py` 不要求你跑（没有真实 main.py），但要保证它的锚点校验逻辑和上一版一致

最后列清楚：新增/修改了哪些文件、新工具的名字和参数、补丁插在哪。
