"""HTML5 document repair before XPath selection, without executing active content."""

from __future__ import annotations

from typing import Any

from lxml import html as _html  # type: ignore[import-untyped]
from selectolax.lexbor import LexborHTMLParser as _Parser  # type: ignore[import-untyped]

from wlh.core import WlhError

html: Any = _html
Parser: Any = _Parser


def document_tree(value: str | bytes) -> Any:
    """Use the browser's table tree rules, then expose the established XPath API.

    Legacy official pages omit opening tr/td tags. An XML-style HTML recovery
    tree may discard their cells or attach them to a wrong row. Lexbor repairs
    the complete original document before content selection, so every caller
    sees the same implied rows and foster-parented text as an HTML5 browser.
    Each invocation owns its parser; no mutable tree is shared across requests.
    """
    try:
        parser = Parser(value, encoding=isinstance(value, bytes), scripting=False)
        serialized = parser.html
        if not serialized:
            raise ValueError
        tree = html.document_fromstring(serialized)
        for comment in list(tree.xpath("//comment()")):
            parent = comment.getparent()
            if parent is None:
                continue
            if comment.tail:
                prev = comment.getprevious()
                if prev is not None:
                    prev.tail = (prev.tail or "") + comment.tail
                else:
                    parent.text = (parent.text or "") + comment.tail
            parent.remove(comment)
        return tree
    except (ValueError, TypeError, RuntimeError):
        raise WlhError("html_unparseable") from None
