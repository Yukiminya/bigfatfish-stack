"""只读扫描 APK 的资源、文本和游戏引擎关键词。"""

import argparse
import codecs
import io
from pathlib import Path, PurePosixPath
import re
import tempfile
import zipfile
import zlib

_LIST_PREFIXES = ("assets/", "lib/arm64-v8a/", "res/raw/")
_TEXT_EXTENSIONS = {".json", ".txt", ".xml", ".csv", ".lua", ".js", ".ini", ".yaml"}
_ENGINE_PATTERN = re.compile(
    r"unity|il2cpp|cocos|lua|mono|air|gamemaker|renpy|cordova|flutter", re.IGNORECASE
)
_NON_ASCII = re.compile(rb"[^\x20-\x7e]+")
_BLOCK_SIZE = 64 * 1024


def _ascii_strings(source):
    pending = bytearray()
    for chunk in iter(lambda: source.read(_BLOCK_SIZE), b""):
        parts = _NON_ASCII.split(chunk)
        pending.extend(parts[0])
        if len(parts) == 1:
            continue
        if len(pending) >= 6:
            yield pending.decode("ascii")
        for part in parts[1:-1]:
            if len(part) >= 6:
                yield part.decode("ascii")
        pending = bytearray(parts[-1])
    if len(pending) >= 6:
        yield pending.decode("ascii")


def _text_lines(source):
    with io.BufferedReader(source) as buffered:
        prefix = buffered.peek(4)[:4]
        encoding = "utf-8-sig"
        if prefix.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
            encoding = "utf-32"
        elif prefix.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            encoding = "utf-16"
        with io.TextIOWrapper(buffered, encoding=encoding, errors="replace") as reader:
            for line in reader:
                yield line.rstrip("\r\n")


def _context(line: str, match: re.Match) -> str:
    start = max(0, match.start() - 60)
    end = min(len(line), match.end() + 80)
    before = "…" if start else ""
    after = "…" if end < len(line) else ""
    return before + line[start:end] + after


def _write_report(archive: zipfile.ZipFile, apk: Path, keywords: list[str], report):
    entries = [entry for entry in archive.infolist() if not entry.is_dir()]
    listing = (entry for entry in entries if entry.filename.startswith(_LIST_PREFIXES))
    largest = sorted(listing, key=lambda entry: (-entry.file_size, entry.filename))[:80]
    print(f"APK：{apk.name}", file=report)
    print("\n资源列表（按解压大小降序，最多 80 项）", file=report)
    for entry in largest:
        print(f"{entry.filename}\t{entry.file_size} 字节", file=report)
    if not largest:
        print("没有匹配的资源文件", file=report)

    print("\n资产关键词命中（文本按行编号，二进制按提取的 ASCII 串编号）", file=report)
    pattern = (
        re.compile("|".join(re.escape(keyword) for keyword in keywords), re.IGNORECASE)
        if keywords else None
    )
    if keywords:
        print("关键词：" + "、".join(keywords), file=report)
    else:
        print("未指定关键词", file=report)

    clues = []
    failures = []
    hit_count = 0
    for entry in entries:
        filename = entry.filename
        asset = filename.startswith("assets/")
        if not (asset or filename.startswith("lib/")):
            continue

        name_matches = {match.group().lower() for match in _ENGINE_PATTERN.finditer(filename)}
        for engine in sorted(name_matches):
            clues.append(f"{engine} | {filename} | 文件名")

        text_file = PurePosixPath(filename).suffix.lower() in _TEXT_EXTENSIONS
        string_matches = {}
        try:
            with archive.open(entry) as source:
                lines = _text_lines(source) if text_file else _ascii_strings(source)
                for line_number, line in enumerate(lines, start=1):
                    if asset and pattern is not None:
                        match = pattern.search(line)
                        if match:
                            print(f"{filename}:{line_number}: {_context(line, match)}", file=report)
                            hit_count += 1
                    for match in _ENGINE_PATTERN.finditer(line):
                        engine = match.group().lower()
                        if engine not in string_matches:
                            string_matches[engine] = (line_number, _context(line, match))
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
            failures.append(f"{filename}: {exc}")

        for engine, (line_number, context) in sorted(string_matches.items()):
            clues.append(f"{engine} | {filename}:{line_number}: {context}")

    if pattern is not None:
        print(f"命中 {hit_count} 行", file=report)

    print("\n脚本与引擎线索（assets/ 和 lib/ 的文件名及内容关键词）", file=report)
    for clue in clues:
        print(clue, file=report)
    if not clues:
        print("没有匹配的引擎关键词", file=report)

    if failures:
        print("\n无法读取的文件", file=report)
        for failure in failures:
            print(failure, file=report)


def scan(apk: Path, keywords: list[str], output: Path) -> None:
    if apk.resolve() == output.resolve() or (output.exists() and apk.samefile(output)):
        raise ValueError("报告路径不能指向 APK 文件")

    temporary_path = None
    try:
        with zipfile.ZipFile(apk, "r") as archive:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=output.parent, prefix=".apk-scan-", delete=False
            ) as report:
                temporary_path = Path(report.name)
                _write_report(archive, apk, keywords, report)
        temporary_path.replace(output)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path, help="要读取的 APK 文件")
    parser.add_argument("--grep", default="", help="逗号分隔的关键词")
    parser.add_argument("--out", type=Path, required=True, help="UTF-8 中文报告路径")
    args = parser.parse_args()
    keywords = [keyword.strip() for keyword in args.grep.split(",") if keyword.strip()]
    try:
        scan(args.apk, keywords, args.out)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"APK 扫描失败：{exc}\n")


if __name__ == "__main__":
    main()
