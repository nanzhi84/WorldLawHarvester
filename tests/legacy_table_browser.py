"""Browser-only observations for complete original HTML and delivered Markdown.

No product parser is imported. Original bytes are decoded once and given directly
to HTML5 DOMParser, preserving omitted tags rather than repairing fixtures first.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any, cast

from markdown_it import MarkdownIt
from playwright.sync_api import Page, sync_playwright

RENDERER = MarkdownIt("commonmark", {"html": True}).enable("table")
BROWSER_READ = r"""input => {
  const doc = new DOMParser().parseFromString(input.markup, 'text/html');
  const root = input.original ? doc.querySelector('#main') : doc.body;
  if (!root) throw new Error('Missing original legal content root');
  if (input.original) root.querySelectorAll('#pankuzu,.pagetop,script,style')
    .forEach(node => node.remove());
  const norm = value => value.normalize('NFKC').replace(/\s/g, '');
  function fragments(node) {
    if (node.nodeType === 3) return node.data.trim() ? [node.data] : [];
    if (node.nodeType !== 1 || ['SCRIPT', 'STYLE'].includes(node.tagName)) return [];
    if (node.tagName === 'IMG') return [node.getAttribute('alt') || ''];
    return Array.from(node.childNodes).flatMap(fragments);
  }
  function observeTable(table) {
    const rows = Array.from(table.rows), slots = {}, overlaps = [];
    let width = 0;
    rows.forEach((row, y) => {
      let x = 0;
      for (const cell of row.cells) {
        while (`${y},${x}` in slots) x++;
        const groupRows = rows.filter(r => r.parentElement === row.parentElement);
        const height = cell.rowSpan || groupRows.length - groupRows.indexOf(row);
        const spanWidth = cell.colSpan;
        if (height > 200 || spanWidth > 200) throw new Error('Oversized fixture span');
        const value = norm(fragments(cell).join(''));
        for (let dy = 0; dy < height; dy++) for (let dx = 0; dx < spanWidth; dx++) {
          const key = `${y + dy},${x + dx}`;
          if (key in slots) overlaps.push(key);
          slots[key] = value;
        }
        x += spanWidth;
        width = Math.max(width, x);
      }
    });
    const grid = rows.map((_, y) => Array.from({length:width}, (_, x) => slots[`${y},${x}`] || ''));
    return {grid, meaningful_grid:grid.filter(row => row.some(Boolean)), overlaps,
      captions:Array.from(table.children).filter(n => n.tagName === 'CAPTION')
        .map(n => norm(fragments(n).join(''))),
      preceding_text:table.previousElementSibling ? norm(fragments(table.previousElementSibling).join('')) : '',
      nested:!!table.parentElement.closest('table'), html:table.outerHTML};
  }
  const parts = fragments(root);
  return {fragments:parts, text:norm(parts.join('')), html:root.innerHTML,
    headings:Array.from(root.querySelectorAll('h1,h2,h3,h4,h5,h6')).map(n => norm(fragments(n).join(''))),
    tables:Array.from(root.querySelectorAll('table')).map(observeTable),
    images:Array.from(root.querySelectorAll('img')).map(n => ({src:n.getAttribute('src'),
      alt:n.getAttribute('alt'),width:n.getAttribute('width'),height:n.getAttribute('height')}))};
}"""


def visible_text(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


class Html5Reader:
    def __init__(self, page: Page, version: str) -> None:
        self.page = page
        self.version = version

    def original(self, payload: bytes, encoding: str) -> dict[str, Any]:
        return cast(dict[str, Any], self.page.evaluate(
            BROWSER_READ, {"markup": payload.decode(encoding), "original": True}
        ))

    def markdown(self, markdown: str) -> dict[str, Any]:
        return cast(dict[str, Any], self.page.evaluate(
            BROWSER_READ, {"markup": RENDERER.render(markdown), "original": False}
        ))


@contextmanager
def html5_reader() -> Generator[Html5Reader]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(java_script_enabled=False)
        page.route("**/*", lambda route: route.abort())
        try:
            yield Html5Reader(page, browser.version)
        finally:
            browser.close()


def missing_fragments(original: dict[str, Any], delivered: dict[str, Any]) -> list[str]:
    """Check all original words/operators in browser order, allowing presentation markers."""
    cursor = 0
    actual: str = delivered["text"]
    missing: list[str] = []
    for fragment in original["fragments"]:
        value = visible_text(fragment)
        if not value:
            continue
        found = actual.find(value, cursor)
        if found < 0:
            missing.append(fragment)
        else:
            cursor = found + len(value)
    return missing


def compare_tables(
    original: dict[str, Any], delivered: dict[str, Any], textual_tables: frozenset[int],
    *, compare_data: bool = True,
) -> dict[str, Any]:
    """Compare meaningful visible coordinates; empty layout rows are not legal data."""
    source: list[dict[str, Any]] = original["tables"]
    actual: list[dict[str, Any]] = delivered["tables"]
    declarations = ["".join(value for row in source[i]["meaningful_grid"] for value in row)
                    for i in sorted(textual_tables)]
    expected = [t for i, t in enumerate(source) if i not in textual_tables and t["meaningful_grid"]]
    observed = [t for t in actual if t["meaningful_grid"] and
                "".join(value for row in t["meaningful_grid"] for value in row) not in declarations]
    if not compare_data:
        expected, observed = [], []
    differences: list[dict[str, Any]] = []
    for index, wanted in enumerate(expected):
        got = observed[index] if index < len(observed) else None
        if got is None or wanted["meaningful_grid"] != got["meaningful_grid"]:
            differences.append({"table": index, "expected_grid": wanted["meaningful_grid"],
                                "actual_grid": got["meaningful_grid"] if got else None})
        if got is not None:
            for caption in wanted["captions"]:
                if caption not in got["captions"] and caption not in got["preceding_text"]:
                    differences.append({"table": index, "caption_missing_or_unassociated": caption})
    if len(observed) != len(expected):
        differences.append({"expected_data_tables": len(expected), "actual_data_tables": len(observed)})
    missing_declarations = [s for s in declarations if s not in delivered["text"]]
    return {"differences": differences, "missing_declarations": missing_declarations,
            "all_relationships_preserved": not differences and not missing_declarations,
            "data_table_comparison_in_scope": compare_data,
            "original_data_tables": len(expected), "delivered_data_tables": len(observed)}
