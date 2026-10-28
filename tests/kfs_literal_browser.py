"""Browser-visible KFS text, literal numbering and paragraph boundary observations.

Only the original payload and delivered Markdown enter this observer. Original
HTML is decoded once and passed directly to Chromium without HTML reserialization.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from playwright.sync_api import Page

from .legacy_table_browser import visible_text
from .legal_presentation_browser import show_document

APPLICATION = [
    {"anchor": "土地の表示", "label": "1."},
    {"anchor": "添付書類", "label": "1."},
    {"anchor": "B　本件条例には", "label": ""},
    {"anchor": "C　P市長は", "label": ""},
]
READ_LINES = r"""input => {
  const root = document.querySelector(input.original ? '#main' : 'body');
  const norm = s => s.normalize('NFKC').replace(/\s/g, '');
  const lines = root.innerText.split('\n').filter(s => s.trim());
  return input.wanted.map((wanted, index) => {
    const anchor = norm(wanted.anchor), matches = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if (norm(node.data).includes(anchor)) matches.push(node);
    }
    const matchingLines = lines.map((text, line) => ({text, line}))
      .filter(item => norm(item.text).includes(anchor));
    if (matches.length !== 1 || matchingLines.length !== 1)
      return {anchor, matches:matches.length, line_matches:matchingLines.length};
    node = matches[0];
    const line = matchingLines[0], prefix = norm(line.text).split(anchor)[0];
    const owner = node.parentElement.closest('li,[role="listitem"]');
    if (owner) owner.setAttribute(`data-literal-owner-${index}`, '');
    const range = document.createRange();
    range.selectNodeContents(node);
    const rect = range.getBoundingClientRect();
    return {anchor, matches:1, line_matches:1, line:line.line, line_text:line.text,
      literal_prefix:prefix, owner:owner ? String(index) : null,
      owner_contains_application:owner ? ['土地の表示', '添付書類']
        .some(word => norm(owner.innerText).includes(norm(word))) : false,
      top:rect.top + window.scrollY, html:node.parentElement.outerHTML};
  });
}"""


def application_lines(page: Page, *, original: bool) -> list[dict[str, Any]]:
    observed = cast(list[dict[str, Any]], page.evaluate(
        READ_LINES, {"original": original, "wanted": APPLICATION}
    ))
    session: Any = page.context.new_cdp_session(page)
    try:
        document = session.send("DOM.getDocument")
        for item, expected in zip(observed, APPLICATION, strict=True):
            markers: list[str] = []
            if item.get("owner") is not None:
                selected = session.send("DOM.querySelector", {
                    "nodeId": document["root"]["nodeId"],
                    "selector": f'[data-literal-owner-{item["owner"]}]',
                })
                backend = session.send("DOM.describeNode", {"nodeId": selected["nodeId"]})[
                    "node"]["backendNodeId"]
                nodes = session.send("Accessibility.getPartialAXTree", {
                    "backendNodeId": backend, "fetchRelatives": True,
                })["nodes"]
                owner = next(n for n in nodes if n.get("backendDOMNodeId") == backend)
                markers = [n.get("name", {}).get("value", "") for n in nodes
                           if n["nodeId"] in owner.get("childIds", [])
                           and n.get("role", {}).get("value") == "ListMarker"
                           and not n["ignored"]]
            item["generated_markers"] = markers
            item["displayed_prefix"] = visible_text(
                "".join(markers) + item.get("literal_prefix", "")
            )
            item["expected_prefix"] = expected["label"]
            item["label_and_line_boundary_preserved"] = (
                item.get("matches") == item.get("line_matches") == 1
                and item["displayed_prefix"] == expected["label"]
            )
    finally:
        session.detach()
    return observed


def subtitle_position(page: Page, *, original: bool) -> dict[str, Any]:
    return cast(dict[str, Any], page.evaluate(r"""original => {
      const root = document.querySelector(original ? '#main' : 'body');
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      const matches = []; let node;
      while ((node = walker.nextNode())) {
        if (node.data.includes('貸付金評価額（総括表）')) matches.push(node);
      }
      const tables = Array.from(root.querySelectorAll('table'));
      if (matches.length !== 1) return {matches:matches.length, tables:tables.length};
      node = matches[0];
      const range = document.createRange(); range.selectNodeContents(node);
      const rect = range.getBoundingClientRect();
      return {matches:1, tables:tables.length, visible:rect.width > 0 && rect.height > 0,
        in_table:!!node.parentElement.closest('table'),
        precedes_every_table:tables.every(table => !!(node.compareDocumentPosition(table) & 4)),
        text:node.data, top:rect.top + window.scrollY};
    }""", original))


def capture_document(
    page: Page, content: str, directory: Path, kind: str, *, original: bool
) -> dict[str, Any]:
    show_document(page, content, original=original)
    page.set_viewport_size({"width": 1440, "height": 1200})
    label = "original" if original else "delivered"
    root = page.locator("#main" if original else "body")
    root.screenshot(path=str(directory / f"{label}.png"))
    result: dict[str, Any] = {}
    if kind == "application":
        lines = application_lines(page, original=original)
        result["application_lines"] = lines
        top = next((item["top"] for item in lines if "top" in item), 0)
    elif kind == "subtitle":
        result["subtitle"] = subtitle_position(page, original=original)
        top = result["subtitle"].get("top", 0)
    else:
        return result
    page.evaluate("top => window.scrollTo(0, Math.max(0, top - 180))", top)
    page.screenshot(path=str(directory / f"{label}-focus.png"))
    return result
