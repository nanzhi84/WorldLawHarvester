"""Observe accessible row headers and displayed list markers in a real browser.

Source inline styles and native HTML semantics remain active. Site scripts and
external requests are disabled by the shared browser fixture. No product imports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from playwright.sync_api import Page

from .legacy_table_browser import RENDERER, visible_text


def show_document(page: Page, content: str, *, original: bool) -> None:
    page.set_content(content if original else RENDERER.render(content), wait_until="domcontentloaded")


def row_headers(page: Page, *, original: bool) -> list[dict[str, Any]]:
    root = page.locator("#main" if original else "body")
    return [cast(dict[str, Any], header.evaluate("""node => {
      const row = node.closest('tr,[role="row"]');
      return {label:node.innerText, row_text:row ? row.innerText : null,
        html:node.outerHTML, row_html:row ? row.outerHTML : null};
    }""")) for header in root.get_by_role("rowheader").all()]


def header_relationships(headers: list[dict[str, Any]]) -> list[dict[str, str | None]]:
    return [{"label": visible_text(h["label"]),
             "row_text": visible_text(h["row_text"]) if h["row_text"] is not None else None}
            for h in headers]


def source_literal_labels(page: Page) -> list[dict[str, str]]:
    """Read the explicit legal labels from every source list with suppressed markers."""
    return cast(list[dict[str, str]], page.locator("#main").evaluate(r"""root => {
      const norm = s => s.normalize('NFKC').replace(/\s/g, '');
      const result = [];
      for (const item of root.querySelectorAll('li')) {
        if (getComputedStyle(item).listStyleType !== 'none') continue;
        const walker = document.createTreeWalker(item, NodeFilter.SHOW_TEXT);
        let node, first;
        while ((node = walker.nextNode())) {
          if (node.data.trim() && node.parentElement.closest('li') === item) {
            first = node.data; break;
          }
        }
        const match = first && first.trim().match(/^(\d+|\(\d+\))\s+([\s\S]+)/);
        if (!match) continue;
        result.push({label:match[1], anchor:norm(match[2]).slice(0,60),
          first_fragment:first, body_fragment:match[2]});
      }
      return result;
    }"""))


def displayed_labels(
    page: Page, expected: list[dict[str, str]], *, original: bool
) -> list[dict[str, Any]]:
    """Count literal prefixes and browser AX ListMarker names separately.

    A clause may use plain text, a list with hidden markers, or one equivalent
    generated marker. The observer does not require a particular HTML tag.
    """
    observed = cast(list[dict[str, Any]], page.evaluate(r"""input => {
      const root = document.querySelector(input.original ? '#main' : 'body');
      const norm = s => s.normalize('NFKC').replace(/\s/g, '');
      return input.expected.map((wanted, index) => {
        const candidates = Array.from(root.querySelectorAll('*')).filter(node =>
          !['SCRIPT', 'STYLE'].includes(node.tagName) &&
          norm(node.textContent).includes(wanted.anchor) &&
          !Array.from(node.children).some(child => norm(child.textContent).includes(wanted.anchor)));
        if (candidates.length !== 1) return {label:wanted.label, matches:candidates.length};
        const node = candidates[0], text = norm(node.innerText);
        const prefix = text.slice(0, text.indexOf(wanted.anchor));
        const owner = node.closest('li,[role="listitem"]');
        const hasOwnerMarker = owner && norm(owner.innerText).startsWith(prefix + wanted.anchor);
        if (hasOwnerMarker) owner.setAttribute('data-acceptance-number', String(index));
        return {label:wanted.label, matches:1, literal_prefix:prefix,
          marker_owner:hasOwnerMarker ? String(index) : null, html:node.outerHTML,
          list_style:hasOwnerMarker ? getComputedStyle(owner).listStyleType : null};
      });
    }""", {"original": original, "expected": expected}))
    # CDP is a JSON protocol; Playwright leaves its payload mapping untyped.
    session: Any = page.context.new_cdp_session(page)
    try:
        document = session.send("DOM.getDocument")
        for observation in observed:
            markers: list[str] = []
            owner = observation.get("marker_owner")
            if owner is not None:
                selector = f'[data-acceptance-number="{owner}"]'
                node = session.send("DOM.querySelector", {
                    "nodeId": document["root"]["nodeId"], "selector": selector,
                })
                backend = session.send("DOM.describeNode", {"nodeId": node["nodeId"]})[
                    "node"]["backendNodeId"]
                nodes = session.send("Accessibility.getPartialAXTree", {
                    "backendNodeId": backend, "fetchRelatives": True,
                })["nodes"]
                target = next(n for n in nodes if n.get("backendDOMNodeId") == backend)
                markers = [n.get("name", {}).get("value", "") for n in nodes
                           if n["nodeId"] in target.get("childIds", [])
                           and n.get("role", {}).get("value") == "ListMarker"
                           and not n["ignored"]]
            observation["generated_markers"] = markers
            combined = visible_text("".join(markers) + observation.get("literal_prefix", ""))
            label = observation["label"]
            # Decimal list punctuation is presentation; a second index is not.
            canonical = combined.removesuffix(".") if label.isdecimal() else combined
            observation["displayed_label"] = combined
            observation["exactly_one_expected_label"] = (
                observation["matches"] == 1 and canonical == label
            )
    finally:
        session.detach()
    return observed


def body_without_separately_checked_labels(
    original: dict[str, Any], labels: list[dict[str, str]]
) -> dict[str, Any]:
    """Leave legal words/amounts intact; index visibility is checked through the AX tree."""
    replacements = {item["first_fragment"]: item["body_fragment"] for item in labels}
    return {**original, "fragments": [replacements.get(f, f) for f in original["fragments"]]}


def capture_focus(page: Page, directory: Path, kind: str, *, original: bool) -> None:
    root = page.locator("#main" if original else "body")
    focus = (root.locator('table,[role="table"]').filter(has_text="総所得金額").first
             if kind == "row-headers" else root)
    focus.screenshot(path=str(directory / ("original.png" if original else "delivered.png")))
