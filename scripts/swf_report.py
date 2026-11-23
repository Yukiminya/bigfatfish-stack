#!/usr/bin/env python3
"""从 FFDec 导出的 AS3 源码提取事件、状态变量和 xray 线索。"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from html import escape
import json
from pathlib import Path, PurePosixPath
import re
from tempfile import TemporaryDirectory
from typing import Iterator

IDENTIFIER = re.compile(r"(?:[^\W\d]|\$)[\w$]*")
TOKENS = re.compile(
    r"""(?P<skip>\s+|//[^\r\n]*|/\*[\s\S]*?\*/)"""
    r"""|"(?:\\[\s\S]|[^"\\])*"|'(?:\\[\s\S]|[^'\\])*'"""
    r"""|(?:[^\W\d]|\$)[\w$]*|::|===|!==|==|!=|\+\+|--|[+*/%&|^\-]=|[^\s]"""
)
REGEX_LITERAL = re.compile(r"/(?:\\.|\[(?:\\.|[^\\\]\r\n])*\]|[^/\\[\r\n])+/[a-zA-Z]*")
REGEX_PREFIXES = {"=", "(", "[", "{", ",", ":", ";", "!", "?", "return", "throw", "case"}
MODIFIERS = {"public", "private", "protected", "internal", "static", "final", "override", "native"}
NON_CALLS = {"if", "for", "while", "switch", "catch", "with", "function", "return", "throw"}
LEGACY_EVENTS = {"onRelease", "onPress", "onMouseDown", "onMouseUp", "onMouseMove"}
DRAG_CALLS = {"startDrag", "stopDrag"}
REGISTRATION_NAMES = DRAG_CALLS | LEGACY_EVENTS | {"addEventListener"}
KNOWN_EVENTS = {
    "CLICK", "MOUSE_DOWN", "MOUSE_UP", "MOUSE_MOVE", "ROLL_OVER", "ROLL_OUT", "ENTER_FRAME",
}
MEMBER = {".", "::"}
XRAY = re.compile("xray", re.IGNORECASE)
SCENE = re.compile(r"(?:^|/)sc/(?:[^/]+/)*[^/]+_v\.as$")
WRITE_OPERATORS = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "++", "--"}


@dataclass(slots=True)
class Token:
    text: str
    start: int
    end: int
    line: int


def tokenize(source: str) -> list[Token]:
    tokens = []
    position = 0
    line = 1
    while position < len(source):
        match = TOKENS.match(source, position)
        if match is None:
            break
        value = match.group()
        end = match.end()
        if match.lastgroup != "skip":
            if value == "/" and (not tokens or tokens[-1].text in REGEX_PREFIXES):
                literal = REGEX_LITERAL.match(source, position)
                if literal is not None:
                    value, end = literal.group(), literal.end()
            tokens.append(Token(value, position, end, line))
        line += value.count("\n")
        position = end
    return tokens


def matching_pairs(tokens: list[Token]) -> dict[int, int]:
    pairs = {}
    stack = []
    closing = {")": "(", "]": "[", "}": "{", ">": "<"}
    for index, token in enumerate(tokens):
        value = token.text
        if value in ("(", "[", "{") or (
            value == "<" and index and tokens[index - 1].text == "."
        ):
            stack.append(index)
        elif stack and value in closing and tokens[stack[-1]].text == closing[value]:
            opening = stack.pop()
            pairs[opening] = index
            pairs[index] = opening
    return pairs


@dataclass(slots=True)
class Source:
    path: str
    raw: str
    tokens: list[Token] = field(init=False)
    pairs: dict[int, int] = field(init=False)
    starts: list[int] = field(init=False)

    def __post_init__(self) -> None:
        self.tokens = tokenize(self.raw)
        self.pairs = matching_pairs(self.tokens)
        self.starts = self.expression_starts()

    def text(self, index: int) -> str:
        return self.tokens[index].text if 0 <= index < len(self.tokens) else ""

    def name(self, index: int) -> bool:
        return IDENTIFIER.fullmatch(self.text(index)) is not None

    def segment(self, start: int, stop: int) -> str:
        if start >= stop:
            return ""
        return self.raw[self.tokens[start].start : self.tokens[stop - 1].end]

    def expression_starts(self) -> list[int]:
        starts = list(range(len(self.tokens)))
        for index, token in enumerate(self.tokens):
            if self.name(index) and index >= 2 and self.text(index - 1) in MEMBER:
                starts[index] = starts[index - 2]
            elif token.text in (")", "]") and index in self.pairs:
                opening = self.pairs[index]
                previous = opening - 1
                before = self.text(previous)
                if before in (")", "]") or (
                    self.name(previous) and before not in NON_CALLS
                ):
                    starts[index] = starts[previous]
                else:
                    starts[index] = opening
        return starts

    def boundary(self, start: int, stop: int, separators: set[str]) -> int:
        """从当前位置跳过完整的嵌套括号，找下一个顶层分隔符。"""
        while start < stop:
            value = self.text(start)
            if value in separators:
                break
            partner = self.pairs.get(start, start)
            start = partner + 1 if start < partner < stop else start + 1
        return start

    def split(self, start: int, stop: int) -> list[tuple[int, int]]:
        parts = []
        while start < stop:
            end = self.boundary(start, stop, {","})
            parts.append((start, end))
            start = end + 1
        return parts

    def unwrap(self, start: int, stop: int) -> tuple[int, int]:
        while start < stop and self.text(start) == "(" and self.pairs.get(start) == stop - 1:
            start, stop = start + 1, stop - 1
        return start, stop

    def identifier_path(self, start: int, stop: int) -> str | None:
        start, stop = self.unwrap(start, stop)
        if start >= stop or (stop - start) % 2 == 0:
            return None
        for index in range(start, stop):
            if (index - start) % 2 == 0:
                if not self.name(index):
                    return None
            elif self.text(index) not in MEMBER:
                return None
        return "".join(self.text(index) for index in range(start, stop))

    def receiver(self, member: int) -> tuple[str, str]:
        if self.text(member - 1) not in MEMBER or member < 2:
            return "this", "this"
        start = self.starts[member - 2]
        raw = self.segment(start, member - 1)
        key = "".join(self.text(index) for index in range(start, member - 1))
        return raw, key


@dataclass(slots=True)
class Analysis:
    files: list[str] = field(default_factory=list)
    classes: list[dict] = field(default_factory=list)
    functions: dict[str, dict] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    variables: list[dict] = field(default_factory=list)
    xray: list[dict] = field(default_factory=list)
    xray_writes: list[dict] = field(default_factory=list)
    ui_identifiers: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))


def modifiers_at(source: Source, index: int) -> tuple[int, set[str]]:
    start = index
    while source.text(start - 1) in MODIFIERS:
        start -= 1
    return start, {source.text(i) for i in range(start, index)}


def read_function(
    source: Source, index: int, cls: dict | None, package: str,
    parent: str | None, direct_method: bool,
) -> tuple[dict, int, int] | None:
    head = index + 1
    accessor = None
    if source.text(head) in ("get", "set") and source.name(head + 1):
        accessor, head = source.text(head), head + 1
    name = source.text(head) if source.name(head) else None
    params = head + 1 if name else head
    close = source.pairs.get(params)
    if source.text(params) != "(" or close is None:
        return None
    body = close + 1
    if source.text(body) == ":":
        body = source.boundary(body + 1, len(source.tokens), {"{", ";", "}"})
    if source.text(body) not in ("{", ";"):
        return None
    end = source.pairs.get(body) if source.text(body) == "{" else body
    if end is None:
        return None
    start, modifiers = modifiers_at(source, index)
    identifier = f"{source.path}:{source.tokens[index].start}"
    info = {
        "id": identifier,
        "class": cls["name"] if cls else None,
        "package": package,
        "name": name,
        "accessor": accessor,
        "file": source.path,
        "start_line": source.tokens[start].line,
        "end_line": source.tokens[end].line,
        "static": "static" in modifiers,
        "parameters": [source.segment(a, b) for a, b in source.split(params + 1, close)],
        "parent_id": parent,
        "method": bool(direct_method and name),
        "calls": [],
    }
    return info, body, params


def read_string(token: str) -> str | None:
    if len(token) < 2 or token[0] not in ("'", '"') or token[-1] != token[0]:
        return None
    escapes = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v"}

    def unescape(match: re.Match) -> str:
        value = match.group(1)
        if len(value) > 1 and value[0] in ("u", "x"):
            return chr(int(value[1:], 16))
        return escapes.get(value, value)

    decoded = re.sub(r"\\(u[\da-fA-F]{4}|x[\da-fA-F]{2}|[\s\S])", unescape, token[1:-1])
    if re.search(r"[\ud800-\udfff]", decoded):
        decoded = decoded.encode("utf-16-le", errors="surrogatepass").decode(
            "utf-16-le", errors="replace"
        )
    return decoded


def initial_value(source: Source, start: int, stop: int) -> tuple[str, str]:
    raw = source.segment(start, stop)
    if (
        source.text(start) != "new"
        or source.text(start + 2) != "("
        or source.pairs.get(start + 2) != stop - 1
    ):
        return raw, raw
    kind = source.text(start + 1)
    inner = source.segment(start + 3, stop - 1)
    integer = r"[+-]?(?:0[xX][0-9a-fA-F]+|\d+)"
    number = r"[+-]?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|Infinity|NaN)"
    if (
        (kind == "Boolean" and inner in {"true", "false"})
        or (kind == "String" and stop == start + 5 and read_string(inner) is not None)
        or (kind in {"int", "uint"} and re.fullmatch(integer, inner))
        or (kind == "Number" and re.fullmatch(number, inner))
    ):
        return raw, inner
    return raw, raw


def read_static_variables(source: Source, index: int, limit: int, cls: dict) -> Iterator[dict]:
    stop = source.boundary(index + 1, limit, {";"})
    for start, end in source.split(index + 1, stop):
        if not source.name(start):
            continue
        equals = source.boundary(start + 1, end, {"="})
        raw, value = initial_value(source, equals + 1, end)
        yield {
            "class": cls["name"],
            "file": source.path,
            "line": source.tokens[start].line,
            "name": source.text(start),
            "type": source.segment(start + 2, equals) if source.text(start + 1) == ":" else None,
            "initializer": raw or None,
            "value": value or None,
            "ui_references": [],
        }


def event_type(source: Source, start: int, stop: int) -> str:
    start, stop = source.unwrap(start, stop)
    string = None
    if stop == start + 1:
        string = read_string(source.text(start))
    elif (
        stop == start + 5 and source.text(start) == "new" and source.text(start + 1) == "String"
        and source.text(start + 2) == "("
        and source.pairs.get(start + 2) == stop - 1
    ):
        string = read_string(source.text(start + 3))
    if string is not None:
        key = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", string).upper()
        return key if key in KNOWN_EVENTS else string
    name = source.identifier_path(start, stop)
    if name is not None:
        return re.split(r"\.|::", name)[-1]
    return source.segment(start, stop)


def read_event(
    source: Source, index: int, owner: str | None, cls: str | None
) -> dict | None:
    name = source.text(index)
    following = source.text(index + 1)
    handler_range = None
    expression = None
    if name in DRAG_CALLS | {"addEventListener"} and following == "(":
        close = source.pairs.get(index + 1)
        if close is None:
            return None
        end = close + 1
        syntax = "drag" if name in DRAG_CALLS else "listener"
        event = name
        if syntax == "listener":
            arguments = source.split(index + 2, close)
            if len(arguments) < 2 or any(a == b for a, b in arguments[:2]):
                return None
            expression = source.segment(*arguments[0])
            event = event_type(source, *arguments[0])
            handler_range = arguments[1]
    elif name in LEGACY_EVENTS and following == "=" and source.text(index - 1) in MEMBER:
        end = source.boundary(index + 2, len(source.tokens), {";", ",", "}", ")", "]"})
        event, syntax = name, "assignment"
        handler_range = (index + 2, end)
    else:
        return None

    receiver, receiver_key = source.receiver(index)
    handler_expression = source.segment(*handler_range) if handler_range else None
    handler = handler_expression
    inline_token = None
    if handler_range:
        start, stop = source.unwrap(*handler_range)
        if source.text(start) == "function":
            inline_token = start
            handler = "(内联)"
        elif stop == start + 1 and source.text(start) in {"null", "undefined"}:
            handler = None
        else:
            handler = source.identifier_path(start, stop) or handler

    return {
        "file": source.path,
        "line": source.tokens[index].line,
        "end_line": source.tokens[end - 1].line,
        "class": cls,
        "owner_id": owner,
        "object": receiver,
        "object_key": receiver_key,
        "event": event,
        "event_expression": expression,
        "syntax": syntax,
        "handler": handler,
        "handler_expression": handler_expression if inline_token is None else None,
        "inline_token": inline_token,
        "inline": None,
        "definition_ids": [],
        "resolution": None,
    }


def parse_source(source: Source, result: Analysis) -> None:
    package, package_end = "", -1
    cls, class_open, class_end = None, -1, -1
    braces = []
    function_bodies = {}
    function_tokens = {}
    signature_names = set()
    active_functions = []
    event_start = len(result.events)
    track_ui = PurePosixPath(source.path).name in {"BTN.as", "UI.as"}
    raw_lines = source.raw.splitlines()
    xray_lines = {i for i, line in enumerate(raw_lines, 1) if XRAY.search(line)}
    xray_owners = {}

    for index, token in enumerate(source.tokens):
        value = token.text
        next_value = source.text(index + 1)
        if index == package_end:
            package = ""
        if index == class_end:
            cls = None
        if active_functions and index == active_functions[-1][0]:
            active_functions.pop()
        owner = active_functions[-1][1] if active_functions else None
        class_name = cls["name"] if cls else None
        outer_brace = braces[-1] if braces else -1

        if value == "package" and not braces:
            opening = source.boundary(index + 1, len(source.tokens), {"{", ";"})
            if source.text(opening) == "{" and opening in source.pairs:
                package = "".join(source.text(i) for i in range(index + 1, opening))
                package_end = source.pairs[opening]
        elif (
            value == "class" and cls is None and source.name(index + 1)
            and (not braces or source.pairs.get(outer_brace) == package_end)
        ):
            opening = source.boundary(index + 2, len(source.tokens), {"{", ";"})
            if source.text(opening) == "{" and opening in source.pairs:
                short_name = next_value
                cls = {
                    "name": f"{package}.{short_name}" if package else short_name,
                    "short_name": short_name,
                    "package": package,
                    "file": source.path,
                    "line": token.line,
                    "method_ids": [],
                }
                result.classes.append(cls)
                class_open, class_end = opening, source.pairs[opening]
        elif value == "function":
            function = read_function(
                source, index, cls, package, owner,
                cls is not None and outer_brace == class_open and owner is None,
            )
            if function is not None:
                info, opening, params = function
                identifier = info["id"]
                result.functions[identifier] = info
                function_tokens[index] = identifier
                signature_names.add(params - 1)
                if source.text(opening) == "{":
                    function_bodies[opening] = identifier
                if cls is not None and info["method"]:
                    cls["method_ids"].append(identifier)
        elif (
            value == "var" and cls is not None and outer_brace == class_open
            and SCENE.search(source.path) and "static" in modifiers_at(source, index)[1]
        ):
            result.variables.extend(read_static_variables(source, index, class_end, cls))

        if value == "{":
            braces.append(index)
            if index in function_bodies:
                owner = function_bodies[index]
                active_functions.append((source.pairs[index], owner))
        elif value == "}" and braces and source.pairs.get(index) == braces[-1]:
            braces.pop()

        if token.line in xray_lines and owner is not None:
            xray_owners[token.line] = owner

        if track_ui and source.name(index):
            previous = source.text(index - 1)
            result.ui_identifiers[value].append({
                "file": source.path,
                "line": token.line,
                "write": next_value in WRITE_OPERATORS or previous in {"++", "--"},
            })

        if value == "visible" and source.text(index - 1) in MEMBER and next_value == "=":
            object_text, object_key = source.receiver(index)
            if XRAY.search(object_key):
                end = source.boundary(index + 2, len(source.tokens), {";", ",", "}", ")", "]"})
                raw, normalized = initial_value(source, index + 2, end)
                result.xray_writes.append({
                    "file": source.path, "line": token.line, "object": object_text,
                    "owner_id": owner, "value": normalized, "expression": raw,
                })

        if index in signature_names:
            continue
        if owner is not None and next_value == "(" and source.name(index) and value not in NON_CALLS:
            start = source.starts[index]
            result.functions[owner]["calls"].append({
                "name": source.identifier_path(start, index + 1)
                or source.segment(start, index + 1),
                "line": token.line,
            })
        if value in REGISTRATION_NAMES:
            event = read_event(source, index, owner, class_name)
            if event is not None:
                result.events.append(event)

    for event in result.events[event_start:]:
        inline_token = event.pop("inline_token")
        identifier = function_tokens.get(inline_token)
        if identifier is not None:
            info = result.functions[identifier]
            event["inline"] = {"start_line": info["start_line"], "end_line": info["end_line"]}
            event["definition_ids"] = [identifier]
            event["resolution"] = "inline"

    for line in sorted(xray_lines):
        first, last = max(1, line - 15), min(len(raw_lines), line + 15)
        result.xray.append({
            "file": source.path, "line": line, "start_line": first, "end_line": last,
            "owner_id": xray_owners.get(line), "lines": raw_lines[first - 1 : last],
        })


class Symbols:
    """按词法作用域、类名和包名索引可引用的函数。"""

    def __init__(self, analysis: Analysis) -> None:
        self.functions = analysis.functions
        self.members = defaultdict(list)
        self.locals = defaultdict(list)
        self.packages = defaultdict(list)
        self.named = defaultdict(list)
        self.classes = {cls["name"]: cls for cls in analysis.classes}
        self.short_classes = defaultdict(list)
        for name, cls in self.classes.items():
            self.short_classes[cls["short_name"]].append(name)
        for identifier, function in self.functions.items():
            name = function["name"]
            if name is None:
                continue
            self.named[name].append(identifier)
            if function["parent_id"] is not None:
                self.locals[function["parent_id"], name].append(identifier)
            elif function["method"]:
                self.members[function["class"], name].append(identifier)
            elif function["class"] is None:
                self.packages[function["package"], name].append(identifier)

    def resolve(
        self, expression: str, cls: str | None, owner: str | None
    ) -> tuple[list[str], str]:
        parts = re.split(r"\.|::", expression)
        if not all(IDENTIFIER.fullmatch(part) for part in parts):
            return [], "expression"
        package = self.classes[cls]["package"] if cls in self.classes else (
            self.functions[owner]["package"] if owner is not None else ""
        )
        name = parts[-1]
        if len(parts) == 1:
            parent = owner
            while parent is not None:
                local = self.locals.get((parent, name))
                if local:
                    return local, "scope"
                parent = self.functions[parent]["parent_id"]
            exact = self.members.get((cls, name)) or self.packages.get((package, name))
            if exact:
                return exact, "scope"
        else:
            qualifier = ".".join(parts[:-1])
            if qualifier == "this":
                return self.members.get((cls, name), []), "scope"
            if qualifier in self.classes:
                return self.members.get((qualifier, name), []), "class"
            qualified = f"{package}.{qualifier}" if package else qualifier
            if qualified in self.classes:
                return self.members.get((qualified, name), []), "class"
            possible_classes = self.short_classes.get(qualifier, [])
            if possible_classes:
                choices = [
                    identifier
                    for class_name in possible_classes
                    for identifier in self.members.get((class_name, name), [])
                ]
                return choices, "class" if len(possible_classes) == 1 else "candidate"
            if qualifier == "super":
                return [], "unresolved"

        candidates = self.named.get(name, [])
        return candidates, "candidate" if candidates else "unresolved"


def finish_analysis(result: Analysis) -> dict:
    symbols = Symbols(result)
    for variable in result.variables:
        variable["ui_references"] = result.ui_identifiers.get(variable["name"], [])
    by_handler = defaultdict(list)
    registered_ids = set()
    handler_keys = set()
    for event_id, event in enumerate(result.events, 1):
        event["id"] = event_id
        handler = event["handler"]
        if handler is None:
            event["resolution"] = "none"
            continue
        if event["resolution"] is None:
            targets, resolution = symbols.resolve(handler, event["class"], event["owner_id"])
            event["definition_ids"] = targets
            event["resolution"] = resolution if targets or resolution == "expression" else "unresolved"

        targets = event["definition_ids"]
        if len(targets) == 1 and event["resolution"] != "candidate":
            key = ("definition", targets[0])
            registered_ids.add(targets[0])
        else:
            key = ("expression", event["class"] or event["file"], handler)
        by_handler[key].append(event)
        handler_keys.add(key)

    multiple_objects = []
    for key, events in by_handler.items():
        object_keys = {(event["class"] or event["file"], event["object_key"]) for event in events}
        if len(object_keys) > 1:
            multiple_objects.append({
                "handler": events[0]["handler"],
                "function_id": key[1] if key[0] == "definition" else None,
                "event_ids": [event["id"] for event in events],
            })

    dynamic = []
    xray_links = []
    writers = {write["owner_id"] for write in result.xray_writes if write["owner_id"] is not None}
    writer_names = {result.functions[identifier]["name"] for identifier in writers}
    for identifier, function in result.functions.items():
        if identifier not in registered_ids:
            continue
        listener_calls = []
        if identifier in writers:
            xray_links.append({"handler_id": identifier, "writer_id": identifier, "call": None})
        for call in function["calls"]:
            name = re.split(r"\.|::", call["name"])[-1]
            if name == "addEventListener":
                listener_calls.append(call)
            if name in writer_names:
                targets, resolution = symbols.resolve(call["name"], function["class"], identifier)
                if len(targets) == 1 and targets[0] in writers and resolution != "candidate":
                    xray_links.append({
                        "handler_id": identifier, "writer_id": targets[0], "call": call,
                    })
        if listener_calls:
            dynamic.append({"function_id": identifier, "calls": listener_calls})

    return {
        "overview": {
            "file_count": len(result.files),
            "class_count": len(result.classes),
            "method_count": sum(len(cls["method_ids"]) for cls in result.classes),
            "event_count": len(result.events),
            "unique_handler_count": len(handler_keys),
        },
        "files": result.files,
        "classes": result.classes,
        "functions": result.functions,
        "events": result.events,
        "scene_variables": result.variables,
        "cross_references": {
            "multiple_objects": multiple_objects,
            "dynamic_registration": dynamic,
            "xray_links": xray_links,
        },
        "xray": result.xray,
        "xray_writes": result.xray_writes,
    }


def analyze(as_dir: Path) -> dict:
    if not as_dir.is_dir():
        raise ValueError(f"AS 目录不存在：{as_dir}")
    analysis = Analysis()
    for path in sorted(as_dir.rglob("*.as")):
        if path.is_file():
            relative = path.relative_to(as_dir).as_posix()
            analysis.files.append(relative)
            parse_source(Source(relative, path.read_text(encoding="utf-8-sig")), analysis)
    return finish_analysis(analysis)


def cell(value: str | None) -> str:
    if value is None or value == "":
        return "—"
    return escape(" ".join(value.splitlines()), quote=False).replace("|", "&#124;")


def function_label(function: dict) -> str:
    name = function["name"] or "(内联)"
    owner = function["class"] or function["package"]
    return f"{owner}.{name}" if owner else name


def location(function: dict) -> str:
    return f"{function['file']}:{function['start_line']}"


def call_list(calls: list[dict]) -> str:
    return "，".join(f"{call['name']}（第 {call['line']} 行）" for call in calls) or "—"


def render_report(data: dict) -> str:
    figures = data["overview"]
    events, functions = data["events"], data["functions"]
    by_event = {event["id"]: event for event in events}
    lines = [
        "# AS3 事件逻辑报告", "", "## 概览", "",
        f"- 文件数：{figures['file_count']}",
        f"- 类数：{figures['class_count']}",
        f"- 方法数：{figures['method_count']}",
        f"- 事件注册及拖拽调用点数：{figures['event_count']}",
        f"- 处理函数去重数：{figures['unique_handler_count']}",
        "", "## 事件注册总表", "",
        "| 注册位置 | 对象 | 事件 | 处理函数 | 定义位置 | 该函数里调了什么 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    if not events:
        lines.append("| — | — | — | — | — | — |")
    for event in events:
        targets = [functions[identifier] for identifier in event["definition_ids"]]
        definitions = "，".join(
            f"{function_label(target)}（{location(target)}–{target['end_line']}）"
            for target in targets
        )
        if targets and (len(targets) > 1 or event["resolution"] == "candidate"):
            definitions = "候选：" + definitions
        elif not targets and event["handler"]:
            definitions = "未定位"
        calls = "；".join(
            f"{function_label(target)}：{call_list(target['calls'])}"
            for target in targets if target["calls"]
        )
        row = [
            f"{event['file']}:{event['line']}", event["object"], event["event"],
            event["handler"], definitions, calls,
        ]
        lines.append("| " + " | ".join(cell(value) for value in row) + " |")

    lines.extend([
        "", "## 场景类状态变量表", "",
        "| 场景类 | 名字 | 类型 | 初值 | 位置 | BTN / UI 引用 |",
        "| --- | --- | --- | --- | --- | --- |",
    ])
    if not data["scene_variables"]:
        lines.append("| — | — | — | — | — | — |")
    for variable in data["scene_variables"]:
        references = "，".join(
            f"{ref['file']}:{ref['line']}" + ("（赋值样式）" if ref["write"] else "")
            for ref in variable["ui_references"]
        )
        row = [
            variable["class"], variable["name"], variable["type"], variable["value"],
            f"{variable['file']}:{variable['line']}", references,
        ]
        lines.append("| " + " | ".join(cell(value) for value in row) + " |")

    references = data["cross_references"]
    lines.extend(["", "## 值得注意的交叉引用", "", "### 同一处理函数注册到多个对象", ""])
    if not references["multiple_objects"]:
        lines.append("未发现。")
    for group in references["multiple_objects"]:
        identifier = group["function_id"]
        label = function_label(functions[identifier]) if identifier else group["handler"]
        sites = []
        for event_id in group["event_ids"]:
            event = by_event[event_id]
            sites.append(f"{cell(event['object'])}（{cell(event['file'])}:{event['line']}）")
        lines.append(f"- {cell(label)} → {', '.join(sites)}")

    lines.extend(["", "### 处理函数里的动态再注册", ""])
    if not references["dynamic_registration"]:
        lines.append("未发现。")
    for entry in references["dynamic_registration"]:
        function = functions[entry["function_id"]]
        lines.append(
            f"- {cell(function_label(function))}（{cell(location(function))}）"
            f" → {cell(call_list(entry['calls']))}"
        )

    lines.extend(["", "## xray 可见性写入", ""])
    if not data["xray_writes"]:
        lines.append("未发现直接的 .visible 赋值。原文关键字上下文见 xray.md。")
    for write in data["xray_writes"]:
        owner = functions.get(write["owner_id"])
        label = f"（{cell(function_label(owner))}）" if owner else ""
        lines.append(
            f"- {cell(write['file'])}:{write['line']}{label} "
            f"`{cell(write['object'])}.visible = {cell(write['value'])}`"
        )
    if references["xray_links"]:
        lines.extend(["", "### 从事件处理函数到写入位置的直接路径", ""])
        by_definition = defaultdict(list)
        for event in events:
            if len(event["definition_ids"]) == 1 and event["resolution"] != "candidate":
                by_definition[event["definition_ids"][0]].append(event)
        for link in references["xray_links"]:
            handler = functions[link["handler_id"]]
            writer = functions[link["writer_id"]]
            entries = "，".join(
                f"{cell(event['object'])} / {cell(event['event'])}（{cell(event['file'])}:{event['line']}）"
                for event in by_definition[link["handler_id"]]
            )
            chain = cell(function_label(handler))
            if link["call"] is not None:
                chain += f" → {cell(function_label(writer))}（{cell(handler['file'])}:{link['call']['line']}）"
            lines.append(f"- {entries} → {chain}")
    return "\n".join(lines) + "\n"


def render_xray(data: dict) -> str:
    lines = ["# xray 原文上下文", ""]
    if not data["xray"]:
        lines.append("未发现含 xray 的行。")
    for hit in data["xray"]:
        context = "\n".join(hit["lines"])
        backtick_count = max((len(run) for run in re.findall(r"`+", context)), default=0)
        fence = "`" * max(3, backtick_count + 1)
        lines.extend([
            f"### {hit['file']}:{hit['line']}", "",
            f"上下文第 {hit['start_line']}–{hit['end_line']} 行。", "",
            f"{fence}actionscript", context, fence, "",
        ])
    return "\n".join(lines) + "\n"


def write_reports(as_dir: Path, output: Path) -> dict:
    data = analyze(as_dir)
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".swf-report-", dir=output) as workspace:
        temporary = Path(workspace)
        with (temporary / "handlers.json").open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        (temporary / "report.md").write_text(render_report(data), encoding="utf-8")
        (temporary / "xray.md").write_text(render_xray(data), encoding="utf-8")
        for path in temporary.iterdir():
            path.replace(output / path.name)
    return data


def selftest() -> None:
    with TemporaryDirectory(prefix="swf-report-") as directory:
        root = Path(directory)
        scripts = root / "as"
        (scripts / "sc").mkdir(parents=True)
        (scripts / "BTN.as").write_text('''package
{
   public class BTN
   {
      public function wire():void
      {
         this.head
            . addEventListener(MouseEvent.CLICK, onHit);
         m1_v.clock.addEventListener("click", (onHit));
         cells[pick(1, wrap(2))].addEventListener("mouseDown", function(e:MouseEvent):void
         {
            fire(3);
         });
         // ghost.addEventListener(MouseEvent.CLICK, onFake);
         trace("fake.onRelease = function() {}");
         var pattern:RegExp = /[({}]/g;
         m1_v.TIE_UNTIED = true;
      }

      private function onHit(e:MouseEvent):void
      {
         sc.m1_v.highlight(true);
         this.head.addEventListener(Event.ENTER_FRAME, tick);
      }

      private function tick(e:Event):void {}
   }
}
''', encoding="utf-8")
        (scripts / "UI.as").write_text('''package
{
   public class UI
   {
      public function wire():void
      {
         obj.onRelease = function(e:MouseEvent):void
         {
            obj.startDrag(false, Rectangle(1, 2, 3, 4));
            stopDrag();
            sc.m1_v.highlight(false);
         };
      }
   }
}
''', encoding="utf-8")
        (scripts / "sc" / "m1_v.as").write_text('''package sc
{
   public class m1_v
   {
      public static var TIE_UNTIED:Boolean = new Boolean(false);
      private static var headDelay:int = new int(0), prefix:String = new String("p_");
      public static var complex:Vector.<Vector.<int>> = new Vector.<Vector.<int>>();

      public static function highlight(on:Boolean):void
      {
         xray.visible = false;
         xray.visible = on;
      }
   }
}
''', encoding="utf-8")

        output = root / "report"
        data = write_reports(scripts, output)
        figures = data["overview"]
        assert figures["file_count"] == 3 and figures["class_count"] == 3
        assert figures["method_count"] == 5 and figures["event_count"] == 7
        assert figures["unique_handler_count"] == 4
        events = data["events"]
        click = [event for event in events if event["event"] == "CLICK"]
        assert [event["object"] for event in click] == ["this.head", "m1_v.clock"]
        assert len(click[0]["definition_ids"]) == 1
        assert click[0]["definition_ids"] == click[1]["definition_ids"]
        handler = data["functions"][click[0]["definition_ids"][0]]
        assert handler["name"] == "onHit" and handler["parameters"] == ["e:MouseEvent"]
        assert [call["name"] for call in handler["calls"]] == [
            "sc.m1_v.highlight", "this.head.addEventListener",
        ]

        nested = next(event for event in events if event["event"] == "MOUSE_DOWN")
        assert nested["object"] == "cells[pick(1, wrap(2))]"
        assert nested["inline"] == {"start_line": 10, "end_line": 13}
        legacy = next(event for event in events if event["event"] == "onRelease")
        assert legacy["handler"] == "(内联)" and legacy["inline"] is not None
        assert {event["object"] for event in events if event["syntax"] == "drag"} == {"obj", "this"}
        assert [variable["value"] for variable in data["scene_variables"][:3]] == [
            "false", "0", '"p_"',
        ]
        assert data["scene_variables"][3]["type"] == "Vector.<Vector.<int>>"
        assert data["scene_variables"][0]["ui_references"][0]["write"]
        assert data["xray_writes"][0]["value"] == "false"
        assert data["xray_writes"][1]["value"] == "on"
        assert data["cross_references"]["multiple_objects"][0]["handler"] == "onHit"
        assert len(data["cross_references"]["dynamic_registration"]) == 1
        assert len(data["cross_references"]["xray_links"]) == 2
        assert json.loads((output / "handlers.json").read_text(encoding="utf-8")) == data
        assert "### sc/m1_v.as:11" in (output / "xray.md").read_text(encoding="utf-8")
        assert "xray.visible = false;" in (output / "xray.md").read_text(encoding="utf-8")
        assert "BTN.onHit" in (output / "report.md").read_text(encoding="utf-8")
    print("OK")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-dir", type=Path, help="FFDec 导出的 AS3 源码目录")
    parser.add_argument("--out", type=Path, help="三份报告的输出目录")
    parser.add_argument("--selftest", action="store_true", help="在临时目录运行三份合成 AS3 的离线验收")
    args = parser.parse_args()

    if args.selftest:
        if args.as_dir is not None or args.out is not None:
            parser.error("--selftest 单独运行")
        try:
            selftest()
        except (AssertionError, OSError, ValueError) as exc:
            parser.exit(1, f"自测失败：{exc or '解析结果与预期不符'}\n")
        return
    if args.as_dir is None or args.out is None:
        parser.error("请同时填写 --as-dir 和 --out，或单独运行 --selftest")
    try:
        data = write_reports(args.as_dir, args.out)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"生成报告失败：{exc}\n")
    print(f"已分析 {data['overview']['file_count']} 个 AS3 文件。报告目录：{args.out}")


if __name__ == "__main__":
    main()
