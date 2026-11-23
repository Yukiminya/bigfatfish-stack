"""从截图识别青绿色高亮热区。"""

from collections import defaultdict

import numpy as np
from PIL import Image


class _DisjointSet:
    def __init__(self, count: int):
        self.parent = list(range(count))
        self.size = [1] * count

    def find(self, index: int) -> int:
        parent = self.parent
        while index != parent[index]:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(self, left: int, right: int) -> None:
        left, right = self.find(left), self.find(right)
        if left == right:
            return
        if self.size[left] < self.size[right]:
            left, right = right, left
        self.parent[right] = left
        self.size[left] += self.size[right]


def _components(mask: np.ndarray) -> list[tuple]:
    """四邻域连通域。返回左、上、右、下半开边界及前景像素数。"""
    if mask.ndim != 2:
        raise ValueError("掩码必须是二维数组")
    height, width = mask.shape
    if not height or not width:
        return []

    # 批量找每行前景区段的首尾。Python 循环只处理区段。
    edges = np.empty((height, width + 1), dtype=bool)
    edges[:, 0] = mask[:, 0]
    edges[:, 1:-1] = mask[:, 1:] != mask[:, :-1]
    edges[:, -1] = mask[:, -1]
    rows, columns = np.nonzero(edges)
    run_rows = rows[::2]
    row_offsets = np.searchsorted(run_rows, np.arange(height + 1)).tolist()
    starts = columns[::2].tolist()
    ends = columns[1::2].tolist()
    groups = _DisjointSet(len(starts))

    for row in range(1, height):
        previous, current, stop = row_offsets[row - 1 : row + 2]
        previous_end = current
        while previous < previous_end and current < stop:
            if starts[previous] < ends[current] and starts[current] < ends[previous]:
                groups.union(previous, current)
            if ends[previous] <= ends[current]:
                previous += 1
            else:
                current += 1

    bounds = {}
    for index, (y, x0, x1) in enumerate(zip(run_rows.tolist(), starts, ends)):
        root = groups.find(index)
        if root not in bounds:
            bounds[root] = [x0, y, x1, y + 1, x1 - x0]
            continue
        box = bounds[root]
        box[0] = min(box[0], x0)
        box[2] = max(box[2], x1)
        box[3] = y + 1
        box[4] += x1 - x0
    return [tuple(box) for box in bounds.values()]


def _merge_components(components: list[tuple], gap: int) -> list[tuple]:
    if len(components) < 2 or gap <= 0:
        return components

    # 用网格缩小候选范围，再计算两个半开矩形的实际间距。
    tile = max(32, gap)
    tiles = defaultdict(list)
    groups = _DisjointSet(len(components))
    for index, (x0, y0, x1, y1, _) in enumerate(components):
        nearby = set()
        for row in range((y0 - gap) // tile, (y1 + gap) // tile + 1):
            for column in range((x0 - gap) // tile, (x1 + gap) // tile + 1):
                nearby.update(tiles.get((row, column), ()))

        for other in nearby:
            ox0, oy0, ox1, oy1, _ = components[other]
            dx = max(0, x0 - ox1, ox0 - x1)
            dy = max(0, y0 - oy1, oy0 - y1)
            if dx * dx + dy * dy < gap * gap:
                groups.union(index, other)

        for row in range(y0 // tile, (y1 - 1) // tile + 1):
            for column in range(x0 // tile, (x1 - 1) // tile + 1):
                tiles[row, column].append(index)

    bounds = {}
    for index, (x0, y0, x1, y1, pixels) in enumerate(components):
        root = groups.find(index)
        if root not in bounds:
            bounds[root] = [x0, y0, x1, y1, pixels]
            continue
        box = bounds[root]
        box[0] = min(box[0], x0)
        box[1] = min(box[1], y0)
        box[2] = max(box[2], x1)
        box[3] = max(box[3], y1)
        box[4] += pixels
    return [tuple(box) for box in bounds.values()]


def _box(x: int, y: int, w: int, h: int) -> dict:
    return {"x": x, "y": y, "w": w, "h": h, "cx": x + w // 2, "cy": y + h // 2}


def merge_boxes(boxes, gap: int = 12) -> list[dict]:
    """合并间距小于 gap 的框，返回新框。"""
    rectangles = []
    for box in boxes:
        x, y, w, h = (int(box[key]) for key in ("x", "y", "w", "h"))
        if w > 0 and h > 0:
            rectangles.append((x, y, x + w, y + h, w * h))
    return [
        _box(x0, y0, x1 - x0, y1 - y0)
        for x0, y0, x1, y1, _ in _merge_components(rectangles, gap)
    ]


def find_hotspots(
    image_path: str, tolerance: int = 45, min_area: int = 400
) -> list[dict]:
    with Image.open(image_path) as image:
        rgb = np.asarray(image.convert("RGB"))
    red, green, blue = rgb.transpose(2, 0, 1)
    mask = (
        (red < tolerance)
        & (green >= 55)
        & (green <= 125)
        & (blue >= 55)
        & (blue <= 125)
        & (np.abs(green.astype(np.int16) - blue) <= 30)
    )
    boxes = []
    for x0, y0, x1, y1, pixels in _merge_components(_components(mask), gap=12):
        w, h = x1 - x0, y1 - y0
        if pixels >= min_area and w >= 8 and h >= 8:
            boxes.append(_box(x0, y0, w, h))
    boxes.sort(key=lambda box: (-box["w"] * box["h"], box["y"], box["x"]))
    return boxes


def to_screen(boxes, image_size, screen_size) -> list[dict]:
    """按两轴分别缩放像素框，并夹在设备屏幕内。"""
    image_width, image_height = image_size
    width, height = screen_size
    if min(image_width, image_height, width, height) <= 0:
        raise ValueError("截图和屏幕的宽高必须大于零")
    scale_x, scale_y = width / image_width, height / image_height

    result = []
    for box in boxes:
        x, y, w, h = (int(box[key]) for key in ("x", "y", "w", "h"))
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(image_width, x + w), min(image_height, y + h)
        if x0 >= x1 or y0 >= y1:
            continue
        screen_x = min(width - 1, round(x0 * scale_x))
        screen_y = min(height - 1, round(y0 * scale_y))
        screen_w = min(width - screen_x, max(1, round((x1 - x0) * scale_x)))
        screen_h = min(height - screen_y, max(1, round((y1 - y0) * scale_y)))
        result.append(_box(screen_x, screen_y, screen_w, screen_h))
    return result
