"""Deterministic RTF text extraction and mixed Vietnamese legacy-font recovery."""

import re

from wlh.core import WlhError
from wlh.extraction.markdown import compact

TCVN3_CONTRACT = "wlh.tcvn3.v2"

TCVN3_SOURCE = "µ¸¶·¹¨»¾¼½Æ©ÇÊÈÉË®ÌÐÎÏÑªÒÕÓÔÖ×ÝØÜÞßãáâä«åèæçé¬êíëìîïóñòô-õøö÷ùúýûüþ¡¢§£¤¥¦"
TCVN3_TARGET = "àáảãạăằắẳẵặâầấẩẫậđèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵĂÂĐÊÔƠƯ"
_HYPHEN = TCVN3_SOURCE.index("-")
TCVN3_TRANSLATION = str.maketrans(
    {
        **dict(
            zip(
                TCVN3_SOURCE[:_HYPHEN] + TCVN3_SOURCE[_HYPHEN + 1 :],
                TCVN3_TARGET[:_HYPHEN] + TCVN3_TARGET[_HYPHEN + 1 :],
                strict=True,
            )
        ),
        "\u00ad": "ư",
    }
)
LEGACY_TCVN3_FONT = re.compile(r"(?:^|[.\s])(vn(?:time|arial|helvet|book|courier)|vntimeh)\b", re.I)
# Glyphs that do not occur in normal Unicode Vietnamese prose. Other TCVN3 code points
# overlap Unicode letters such as á and à; converting a whole mixed document would
# corrupt already-correct headers.
TCVN3_STRONG_GLYPHS = frozenset("µ¸¶·¹¨»¾¼½Æ©ÇÊÈÉË®ÌÐÎÏÑªÒÕÓÔÖ×ÝØÜÞß«åæ¬ëîïñö÷ûüþ¡¢§£¤¥¦")


def tcvn3_to_unicode(value):
    return value.translate(TCVN3_TRANSLATION)


# Letters that exist only in Unicode Vietnamese (never in cp1252-decoded TCVN3 bytes).
UNICODE_VIETNAMESE = re.compile(
    "[\u0102\u0103\u0110\u0111\u0128\u0129\u0168\u0169\u01a0\u01a1\u01af\u01b0\u1ea0-\u1ef9]"
)
TCVN3_CHARS = frozenset(TCVN3_SOURCE) | {"\u00ad"}


def normalize_detected_tcvn3(value, font_evidence=False):
    """Line-level recovery of a mixed Unicode/TCVN3 export.

    A line that already contains Unicode-only Vietnamese letters (headers, dates) is never
    converted. Otherwise a line is converted when it carries a strong TCVN3 glyph, or any
    TCVN3 code point when the source declares a TCVN3 font (.VnTime, .VnArial, ...).
    """
    if not font_evidence and sum(value.count(char) for char in TCVN3_STRONG_GLYPHS) < 3:
        return value, False
    # Once the document has reliable legacy evidence, short lines such as "Môc 1"
    # still need recovery even though they contain no strong glyph themselves.
    marker = TCVN3_CHARS
    changed = False
    lines = []
    for line in value.splitlines(keepends=True):
        if not UNICODE_VIETNAMESE.search(line) and any(char in marker for char in line):
            converted = tcvn3_to_unicode(line)
            changed |= converted != line
            lines.append(converted)
        else:
            lines.append(line)
    return "".join(lines), changed


def rtf_font_names(raw):
    text = raw.decode("latin-1", errors="ignore")
    return [
        item.strip()
        for item in re.findall(r"(?:\\f\d+[^;{}]*)?([^;{}]+);", text[:20000])
        if "font" not in item.lower()
    ]


def rtf_uses_tcvn3(raw):
    matches = [font for font in rtf_font_names(raw) if LEGACY_TCVN3_FONT.search(font)]
    return bool(matches), sorted(set(matches))[:20]


LEGAL_TOP_HEADING = re.compile(r"^(?:phần|chương)\b", re.I)
LEGAL_SECTION_HEADING = re.compile(r"^(?:mục|tiểu mục)\b", re.I)
LEGAL_ARTICLE_HEADING = re.compile(r"^điều\s+\d+[a-zA-Z]?(?:\s*[.:]|$)", re.I)
LEGAL_TITLE = re.compile(
    r"^(?:luật|bộ luật|nghị định|thông tư|quyết định|nghị quyết|chỉ thị|pháp lệnh)\b", re.I
)


def legal_markdown(value):
    """Restore Vietnamese legal hierarchy: Phần/Chương #, Mục/Tiểu mục ##, Điều ###."""
    # RTF source line wraps are dropped by the RTF state machine (they are not content),
    # so every remaining line break is a real paragraph boundary.
    paragraphs = [compact(item) for item in re.split(r"\n{2,}|\n(?=\S)", value) if compact(item)]
    result, title_budget = [], 2
    for plain in paragraphs:
        if LEGAL_TOP_HEADING.match(plain):
            result.append("# " + plain)
            title_budget = 0  # Document titles only precede the structural body.
        elif LEGAL_SECTION_HEADING.match(plain):
            result.append("## " + plain)
            title_budget = 0
        elif LEGAL_ARTICLE_HEADING.match(plain):
            result.append("### " + plain)
            title_budget = 0
        elif title_budget and LEGAL_TITLE.match(plain):
            result.append("# " + plain)
            title_budget -= 1
        else:
            result.append(plain)
    return "\n\n".join(result).strip() + "\n"


RTF_DESTINATIONS = {
    "fonttbl",
    "colortbl",
    "stylesheet",
    "info",
    "pict",
    "object",
    "header",
    "footer",
    "headerl",
    "headerr",
    "footerl",
    "footerr",
    "headerf",
    "footerf",
    "annotation",
    "fldinst",
    "themedata",
    "datastore",
    "listtable",
    "listoverridetable",
    "rsidtbl",
    "generator",
    "latentstyles",
}
RTF_CONTROL_TEXT = {
    "par": "\n",
    "line": "\n",
    "page": "\n",
    "row": "\n",
    "cell": "\t",
    "tab": "\t",
    "emdash": "—",
    "endash": "–",
    "bullet": "•",
    "lquote": "‘",
    "rquote": "’",
    "ldblquote": "“",
    "rdblquote": "”",
}
RTF_TOKEN = re.compile(
    r"\\([a-zA-Z]{1,32})(-?\d{1,10})?[ ]?|\\'([0-9a-fA-F]{2})|\\([^a-zA-Z])|([{}])|([^\\{}]+)"
)


def rtf_bytes_to_tcvn3_raw(raw):
    """Deterministic pure-Python RTF state machine; no GUI application is launched."""
    text = raw.decode("latin-1", errors="replace")
    output, stack = [], [False]
    unicode_skip, pending_skip = 1, 0
    for match in RTF_TOKEN.finditer(text):
        word, number, hex_byte, symbol, brace, chunk = match.groups()
        if brace == "{":
            stack.append(stack[-1])
            continue
        if brace == "}":
            if len(stack) > 1:
                stack.pop()
            continue
        if symbol:
            if symbol == "*":
                stack[-1] = True
            elif not stack[-1] and pending_skip <= 0:
                output.append({"~": " ", "_": "‑", "-": ""}.get(symbol, symbol))
            continue
        if hex_byte:
            if pending_skip:
                pending_skip -= 1
            elif not stack[-1]:
                output.append(bytes([int(hex_byte, 16)]).decode("cp1252", errors="replace"))
            continue
        if word:
            lower = word.lower()
            if lower in RTF_DESTINATIONS:
                stack[-1] = True
            elif lower == "uc" and number is not None:
                unicode_skip = max(0, int(number))
            elif lower == "u" and number is not None:
                value = int(number)
                if value < 0:
                    value += 65536
                if not stack[-1]:
                    ch = chr(value) if 0 <= value <= 0x10FFFF else "\ufffd"
                    if (
                        0xDC00 <= value <= 0xDFFF
                        and output
                        and len(output[-1]) == 1
                        and 0xD800 <= ord(output[-1]) <= 0xDBFF
                    ):
                        high = ord(output.pop())
                        output.append(chr(0x10000 + ((high - 0xD800) << 10) + (value - 0xDC00)))
                    else:
                        output.append(ch)
                    pending_skip = unicode_skip
            elif not stack[-1] and lower in RTF_CONTROL_TEXT:
                output.append(RTF_CONTROL_TEXT[lower])
            continue
        if chunk:
            chunk = chunk.replace("\r", "").replace("\n", "")
            if pending_skip:
                consumed = min(pending_skip, len(chunk))
                chunk = chunk[consumed:]
                pending_skip -= consumed
            if not stack[-1]:
                output.append(chunk)
    cleaned = ["\ufffd" if len(c) == 1 and 0xD800 <= ord(c) <= 0xDFFF else c for c in output]
    value = compact("".join(cleaned))
    if len(value) < 8:
        raise WlhError("rtf_direct_text_unusable")
    return value + "\n"


def looks_like_tcvn3(value):
    return sum(value.count(char) for char in TCVN3_STRONG_GLYPHS) >= 3
