"""Bounded visible-source evidence; never execute or infer arbitrary JavaScript."""

from __future__ import annotations

import ast
import json
import re
import unicodedata
from html.parser import HTMLParser
from typing import Any, cast

from wlh.benchmark.markdown_evidence import BLOCK_TAGS, VOID_TAGS

STRING = r"""(?:'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")"""
IDENTIFIER = r"[A-Za-z_$][\w$]*"


def string_value(literal: str) -> str | None:
    try:
        value = ast.literal_eval(literal)
    except (SyntaxError, ValueError):
        return None
    return value if isinstance(value, str) else None


def literal_navigation_target(binding: str) -> str | None:
    """Read a complete button click binding containing one literal navigation.

    This attests the declared target, not the contents of the destination. Extra
    statements, variable references and computed destinations remain unsupported.
    """
    match = re.fullmatch(
        rf"\s*click\s*:\s*function\s*\(\s*\)\s*\{{\s*"
        rf"window\.location\s*=\s*({STRING})\s*;?\s*\}}\s*",
        binding,
        re.S,
    )
    return string_value(match[1]) if match else None


def literal_document_output(script: str) -> str | None:
    """Recognize complete, finite literal-output templates used by source tables.

    Only a literal write, a literal variable write, a display-only separator
    replacement, or a literal equality selecting two literal/variable wrappers
    is supported. No eval, arbitrary calls, arithmetic, loops or cross-script state.
    """
    code = script.strip()
    direct = re.fullmatch(rf"document\.write\(\s*({STRING})\s*\)\s*;?", code, re.S)
    if direct:
        return string_value(direct[1])
    initial = re.fullmatch(
        rf"(?:var|let|const)\s+({IDENTIFIER})\s*=\s*({STRING})\s*;\s*(.*)", code, re.S
    )
    if not initial:
        return None
    name, literal, rest = initial.groups()
    value = string_value(literal)
    if value is None:
        return None
    variable = re.escape(name)
    write_variable = rf"document\.write\(\s*{variable}\s*\)\s*;?"
    if re.fullmatch(write_variable, rest, re.S):
        return value
    formatted = re.fullmatch(
        rf"if\s*\(\s*{variable}\s*&&\s*{variable}\.length\s*>\s*0\s*\)\s*\{{\s*"
        rf"{variable}\s*=\s*{variable}\.replaceAll\(\s*({STRING})\s*,\s*({STRING})\s*\)\s*;\s*"
        + write_variable
        + r"\s*\}",
        rest,
        re.S,
    )
    if formatted:
        before, after = (string_value(part) for part in formatted.groups())
        if (
            before
            and after
            and before in {"、", ",", ";", "\n"}
            and re.fullmatch(r"\s*<br\s*/?>\s*", after, re.I)
        ):
            return value.replace(before, after)
        return None
    conditional = re.fullmatch(
        rf"if\s*\(\s*{variable}\s*==\s*({STRING})\s*\)\s*\{{\s*"
        rf"document\.write\(\s*({STRING})\s*\)\s*;?\s*\}}\s*else\s*\{{\s*"
        rf"document\.write\(\s*({STRING})\s*\+\s*{variable}\s*\+\s*({STRING})\s*\)\s*;?\s*\}}",
        rest,
        re.S,
    )
    if conditional:
        values = [string_value(part) for part in conditional.groups()]
        if all(part is not None for part in values):
            sentinel, alternative, before, after = cast(list[str], values)
            return alternative if value == sentinel else before + value + after
    return None


class LiteralDateScripts(HTMLParser):
    """Collect executable inline scripts without executing source JavaScript."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parents: list[str] = []
        self.code: list[str] | None = None
        self.executable = False
        self.scripts: list[tuple[str, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            values = dict(attrs)
            self.code = []
            self.executable = (
                not values.get("src")
                and (values.get("type") or "").lower()
                in {"", "text/javascript", "application/javascript"}
                and not {"template", "noscript", "svg", "textarea"}.intersection(self.parents)
            )
        if tag not in VOID_TAGS:
            self.parents.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.code is not None:
            if self.executable:
                self.scripts.append(("".join(self.code), "head" not in self.parents))
            self.code = None
        if tag in self.parents:
            del self.parents[len(self.parents) - 1 - self.parents[::-1].index(tag) :]

    def handle_data(self, data: str) -> None:
        if self.code is not None:
            self.code.append(data)


def literal_date_outputs(raw: str) -> list[str]:
    """Read a unique literal timestamp displayed by a complete substring write.

    A declaration-only script must precede the complete visible writer. Unused
    variables, branches, local functions, computed values and ambiguous writes
    provide no evidence. This is a finite source template, not a JS interpreter.
    """
    parser = LiteralDateScripts()
    parser.feed(raw)
    declaration = re.compile(rf"\s*(?:var|let|const)\s+({IDENTIFIER})\s*=\s*({STRING})\s*;")
    bindings: dict[str, tuple[str, int]] = {}
    scripts = parser.scripts
    # Query strings such as "&tm=" and comments are not assignments. Remove
    # complete quoted literals first, so URL slashes cannot begin comments.
    assignment_code = [
        re.sub(r"//[^\r\n]*|/\*.*?\*/", " ", re.sub(STRING, " ", code), flags=re.S)
        for code, _ in scripts
    ]
    for index, (code, _) in enumerate(scripts):
        position = 0
        entries: list[tuple[str, str | None]] = []
        while match := declaration.match(code, position):
            entries.append((match[1], string_value(match[2])))
            position = match.end()
        if code[position:].strip():
            continue
        for name, value in entries:
            if value is not None and re.fullmatch(
                r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?", value
            ):
                bindings[name] = (value, index)
    output: list[str] = []
    for index, (code, visible) in enumerate(scripts):
        match = re.fullmatch(
            rf"\s*document\.write\(\s*({IDENTIFIER})\.substring\(\s*0\s*,\s*"
            r"(\d{1,2})\s*\)\s*\)\s*;?\s*",
            code,
        )
        if not visible or not match or match[1] not in bindings:
            continue
        name, length = match[1], int(match[2])
        value, declared_at = bindings[name]
        if declared_at >= index or length < 10:
            continue
        # Any second assignment (including one in an unsupported script) makes
        # the value ambiguous. Never select a convenient declaration from many.
        writes = re.compile(
            rf"(?<![\w$])(?:window\.|globalThis\.)?{re.escape(name)}\s*"
            r"(?:=(?!=)|\+=|-=|\+\+|--)"
        )
        if sum(len(writes.findall(script)) for script in assignment_code) == 1:
            output.append(value[:length])
    return output


class SourceBody(HTMLParser):
    def __init__(self, *, xml: bool = False, dynamic: bool = True) -> None:
        super().__init__(convert_charrefs=True)
        self.xml, self.dynamic = xml, dynamic
        self.blocks: list[str] = []
        self.current: list[str] = []
        self.hidden: list[str] = []
        self.script: list[str] = []
        self.title: list[str] | None = None
        self.link_text: list[str] | None = None

    def flush(self) -> None:
        text = "".join(self.current)
        if text.strip():
            self.blocks.append(text)
        self.current = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title" and self.hidden == ["head"]:
            self.title = []
        if self.hidden:
            if tag not in VOID_TAGS:
                self.hidden.append(tag)
            return
        # Source CSS/hidden state may change at initialization or interaction.
        # Ordinary DOM text is existence evidence; review decides its business
        # scope. Unlike source markup, hidden *output* remains prohibited.
        if tag in {"head", "script", "style", "noscript", "template", "svg"}:
            self.flush()
            if tag not in VOID_TAGS:
                self.hidden.append(tag)
            if tag == "script":
                self.script = []
            return
        if tag == "a":
            self.link_text = []
        if tag in BLOCK_TAGS:
            self.flush()
        for name, value in attrs:
            if value and name in {"alt", "title"}:
                self.blocks.append(value)
            elif value and self.xml:
                self.blocks.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self.title is not None:
            self.blocks.append("".join(self.title))
            self.title = None
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
                if tag == "script" and not self.hidden and self.dynamic:
                    fragment = literal_document_output("".join(self.script))
                    if fragment is not None:
                        child = SourceBody(dynamic=False)
                        child.feed(fragment)
                        child.flush()
                        if re.search(r"<(?:td|th|tr)\b", fragment, re.I):
                            self.flush()
                            self.blocks.extend(child.blocks)
                        else:
                            self.current.append("".join(child.blocks))
            return
        if tag == "a" and self.link_text is not None:
            # Block children can form one visible link label. Joining only
            # within this anchor never invents evidence across unrelated links.
            self.blocks.append("".join(self.link_text))
            self.link_text = None
        if tag in BLOCK_TAGS:
            self.flush()

    def handle_data(self, data: str) -> None:
        if self.title is not None:
            self.title.append(data)
        if self.hidden:
            if self.hidden[-1] == "script":
                self.script.append(data)
            return
        self.current.append(data)
        if self.link_text is not None:
            self.link_text.append(data)


def source_body_blocks(raw: str, mime_type: str) -> list[str]:
    if mime_type == "application/json":
        try:
            value: Any = json.loads(raw)
        except ValueError:
            return []

        def blocks(node: Any) -> list[str]:
            if isinstance(node, str):
                return source_body_blocks(node, "text/html") if "<" in node else [node]
            if isinstance(node, dict):
                return [
                    part for k, v in cast(dict[str, Any], node).items() for part in [k, *blocks(v)]
                ]
            if isinstance(node, list):
                return [part for v in cast(list[Any], node) for part in blocks(v)]
            return [str(node)] if isinstance(node, (int, float)) else []

        return blocks(value)
    parser = SourceBody(xml=mime_type == "application/xml")
    parser.feed(raw)
    parser.flush()
    return parser.blocks + (literal_date_outputs(raw) if mime_type == "text/html" else [])


class DocumentNumberRows(HTMLParser):
    """Read parallel number-name/number lines inside one native metadata row."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.table_depth = 0
        self.row: list[list[str]] | None = None
        self.cell: list[str] | None = None
        self.groups: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "template"}:
            self.hidden += 1
        if tag == "table":
            self.table_depth += 1
            if self.table_depth > 1:
                self.row = self.cell = None
        if self.table_depth != 1:
            return
        if tag == "tr":
            self.row = []
        if tag in {"td", "th"} and self.row is not None:
            self.cell = []
        if tag in {"br", "p", "div"} and self.cell is not None:
            self.cell.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "template"}:
            self.hidden = max(0, self.hidden - 1)
        if tag == "table":
            self.table_depth = max(0, self.table_depth - 1)
            self.row = self.cell = None
        if self.table_depth != 1:
            return
        if tag in {"p", "div"} and self.cell is not None:
            self.cell.append("\n")
        if tag in {"td", "th"} and self.cell is not None and self.row is not None:
            lines = [s.strip() for s in "".join(self.cell).splitlines() if s.strip()]
            self.row.append(lines)
            self.cell = None
        if tag == "tr" and self.row is not None:
            if len(self.row) >= 2:
                names, numbers = self.row[:2]
                if (
                    len(names) == len(numbers) > 1
                    and all(re.fullmatch(r"[^\s\d第]+(?:情報|通達|通知)", s) for s in names)
                    and all(re.fullmatch(r"第\s*\d+\s*号", s) for s in numbers)
                ):
                    self.groups.append("・".join(a + b for a, b in zip(names, numbers)))
            self.row = None

    def handle_data(self, data: str) -> None:
        if self.cell is not None and not self.hidden:
            self.cell.append(unicodedata.normalize("NFKC", data))


def document_number_groups(raw: str) -> list[str]:
    parser = DocumentNumberRows()
    parser.feed(raw)
    return parser.groups
