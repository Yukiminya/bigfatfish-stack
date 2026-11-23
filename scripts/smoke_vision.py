"""离线验收视觉回包和设备坐标换算。"""

import importlib.util
from pathlib import Path
import sys
import types
import unittest

# 电脑上没装 aiohttp 也能跑：视觉模块只在发请求时才用到它。
try:
    import aiohttp  # noqa: F401
except ModuleNotFoundError:
    sys.modules["aiohttp"] = types.ModuleType("aiohttp")

# 支持脚本生成的独立目录，也支持 AstrBot 仓库中的插件路径。
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "vision_look.py"
if not MODULE_PATH.is_file():
    MODULE_PATH = (
        ROOT
        / "astrbot"
        / "plugins"
        / "astrbot_plugin_android_hands"
        / "vision_look.py"
    )

SPEC = importlib.util.spec_from_file_location("vision_look", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"无法加载 {MODULE_PATH}")
vision = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vision)


class SmokeVisionTests(unittest.TestCase):
    def test_fenced_json(self):
        result = vision.parse_reply('```json\n{"app":"游戏","elements":[]}\n```')
        self.assertEqual(result["app"], "游戏")

    def test_text_around_json(self):
        result = vision.parse_reply('模型回复如下：{"desc":"画面有 } 符号"} 后续文字')
        self.assertEqual(result["desc"], "画面有 } 符号")

    def test_invalid_json(self):
        self.assertIsNone(vision.parse_reply('{"elements":,}'))

    def test_missing_elements(self):
        self.assertEqual(vision.parse_reply('{"app":"游戏"}')["elements"], [])
        self.assertEqual(vision.parse_reply('{"elements":{}}')["elements"], [])

    def test_center_coordinates(self):
        elements = [{"name": "开始游戏", "x": 0.5, "y": 0.5}]
        self.assertEqual(
            vision.to_pixels(elements, 1080, 2400),
            [{"name": "开始游戏", "x": 540, "y": 1200, "kind": "area", "confident": False}],
        )

    def test_clamped_coordinates(self):
        elements = [{"x": -0.2, "y": 1.3}]
        result = vision.to_pixels(elements, 1080, 2400)
        self.assertEqual((result[0]["x"], result[0]["y"]), (0, 2399))

    def test_invalid_coordinates(self):
        elements = [
            {"x": "0.5", "y": 0.5},
            {"x": 0.5, "y": None},
            {"x": True, "y": 0.5},
            {"x": 0.5, "y": float("nan")},
        ]
        self.assertEqual(vision.to_pixels(elements, 1080, 2400), [])

    def test_qwen_thousand_scale(self):
        # Qwen-VL 系默认回 0~1000 的相对坐标。
        elements = [{"name": "停止全部", "x": 498, "y": 258, "kind": "button"}]
        self.assertEqual(
            vision.to_pixels(elements, 1080, 2400),
            [{"name": "停止全部", "x": 538, "y": 619, "kind": "button", "confident": False}],
        )

    def test_sent_image_pixels(self):
        # 万一模型直接回上传图的像素，就按上传图尺寸换算。
        elements = [{"x": 288, "y": 1264}]
        result = vision.to_pixels(elements, 1080, 2400, image_size=(576, 1280))
        self.assertEqual((result[0]["x"], result[0]["y"]), (540, 2370))

    def test_device_commands_cover_calls(self):
        """main.py 里调用的每个设备命令都必须登记在 _COMMANDS 里。

        这个坑踩过：_capture 用 self._device("screenshot")，但 _COMMANDS 没登记
        screenshot，于是 phone_look / phone_shot 全部回「设备命令不受支持」。
        """
        import ast

        source = MODULE_PATH.parent.joinpath("main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        declared = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "_COMMANDS"
                for target in node.targets
            ):
                declared = {
                    key.value
                    for key in node.value.keys
                    if isinstance(key, ast.Constant)
                }
        self.assertTrue(declared, "_COMMANDS 没找到")
        used = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "_device"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                continue
            used.add(node.args[0].value)
        self.assertEqual(used - declared, set(), "有设备命令没登记进 _COMMANDS")


if __name__ == "__main__":
    unittest.main()
