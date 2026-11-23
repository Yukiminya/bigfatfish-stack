#!/usr/bin/env python3
"""把模板文件里的 ${VAR} 用环境变量替换掉，校验是合法 JSON 后写出。

用法：render_template.py <src> <dst>
模板里出现 .env 没定义的变量时直接报错，避免把 ${FOO} 原样写进配置。
"""
import json
import os
import re
import sys

VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def render(raw: str) -> str:
    missing = sorted({m.group(1) for m in VAR.finditer(raw) if m.group(1) not in os.environ})
    if missing:
        raise SystemExit("缺少环境变量: " + ", ".join(missing))
    return VAR.sub(lambda m: os.environ[m.group(1)], raw)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    with open(src, encoding="utf-8-sig") as f:
        raw = f.read()
    out = render(raw)
    json.loads(out)  # 校验
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"    -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
