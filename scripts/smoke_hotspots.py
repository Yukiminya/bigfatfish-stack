"""用合成截图离线验收高亮热区识别。"""

import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image, ImageDraw

# 支持当前脚本生成的独立目录和 AstrBot 仓库中的插件路径。
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "hotspots.py"
if not MODULE_PATH.is_file():
    MODULE_PATH = (
        ROOT
        / "astrbot"
        / "plugins"
        / "astrbot_plugin_android_hands"
        / "hotspots.py"
    )

SPEC = importlib.util.spec_from_file_location("hotspots", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"无法加载 {MODULE_PATH}")
hotspots = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hotspots)


class SmokeHotspotsTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "screen.png"

    def make_image(self, *rectangles):
        with Image.new("RGB", (486, 1080), (60, 60, 60)) as image:
            draw = ImageDraw.Draw(image)
            for rectangle in rectangles:
                draw.rectangle(rectangle, fill=(0, 85, 85))
            image.save(self.path)

    def test_merge_six_pixel_gap(self):
        self.make_image((20, 50, 49, 79), (56, 50, 85, 79))
        first = {"x": 20, "y": 50, "w": 30, "h": 30}
        second = {"x": 56, "y": 50, "w": 30, "h": 30}
        expected = [{"x": 20, "y": 50, "w": 66, "h": 30, "cx": 53, "cy": 65}]
        self.assertEqual(hotspots.merge_boxes([first, second]), expected)
        self.assertEqual(len(hotspots.merge_boxes([first, second], gap=6)), 2)
        self.assertEqual(hotspots.find_hotspots(str(self.path)), expected)

    def test_minimum_area(self):
        self.make_image((20, 50, 39, 69))
        self.assertEqual(len(hotspots.find_hotspots(str(self.path), min_area=400)), 1)
        self.assertEqual(hotspots.find_hotspots(str(self.path), min_area=401), [])

    def test_gray_screen(self):
        self.make_image()
        self.assertEqual(hotspots.find_hotspots(str(self.path)), [])

    def test_scaled_screen(self):
        box = {"x": 137, "y": 616, "w": 108, "h": 72, "cx": 191, "cy": 652}
        result = hotspots.to_screen([box], (486, 1080), (1080, 2400))
        expected = {"x": 304, "y": 1369, "w": 240, "h": 160, "cx": 424, "cy": 1449}
        self.assertEqual(len(result), 1)
        for key, value in expected.items():
            self.assertLessEqual(abs(result[0][key] - value), 1)

        edge = {"x": 480, "y": 1070, "w": 20, "h": 30}
        clipped = hotspots.to_screen([edge], (486, 1080), (1080, 2400))[0]
        self.assertEqual(clipped["x"] + clipped["w"], 1080)
        self.assertEqual(clipped["y"] + clipped["h"], 2400)


if __name__ == "__main__":
    unittest.main()
