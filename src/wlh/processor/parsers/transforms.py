"""Declarative parser transforms registry (Stage 3a).

Each transform corresponds to an entry in ``wlh.config.PARSER_TRANSFORMS`` and
operates on local DOM subtrees, text, or attachment metadata without returning
``RawRecord`` or ``RequestSpec`` objects.
"""

from __future__ import annotations

import html as html_lib
import re
from collections.abc import Callable, Mapping
from typing import Any, cast
from urllib.parse import urlsplit

import lxml.etree as _etree  # type: ignore[import-untyped]

from wlh.cleaners.dates import (
    extract_taiwan_law_dates_and_meta,
    infer_missing_dates,
    parse_japanese_date,
)
from wlh.cleaners.text_cleaner import compact, enrich_bare_title
from wlh.config import PARSER_TRANSFORMS
from wlh.core import WlhError

etree: Any = _etree

_INLINE_SCRIPT_CELL_RE = re.compile(
    r"(<td\b[^>]*>\s*)<script\b[^>]*>(.*?)</script>(\s*</td>)",
    re.I | re.S,
)
_JS_VAR_STRING_RE = re.compile(
    r"""(?:var\s+)?([A-Za-z_$][\w$]*)\s*=\s*(?:"((?:\\.|[^"\\])*)"|'((?:\\.|[^'\\])*)')""",
    re.S,
)
_JS_REPLACE_CALL_RE = re.compile(
    r"""\.replace\(\s*(?:"((?:\\.|[^"\\])*)"|'((?:\\.|[^'\\])*)')\s*,\s*(?:"((?:\\.|[^"\\])*)"|'((?:\\.|[^'\\])*)')\s*\)""",
    re.S,
)
_JS_DOC_WRITE_VAR_RE = re.compile(
    r"""document\.write\(\s*([A-Za-z_$][\w$]*)\s*\)""",
    re.I,
)
_JS_DOC_WRITE_LIT_RE = re.compile(
    r"""document\.write\(\s*(?:"((?:\\.|[^"\\])*)"|'((?:\\.|[^'\\])*)')\s*\)""",
    re.I | re.S,
)

_COMMENCEMENT_RE = re.compile(
    r"この(?:法律|政令|省令|府令|規則|勅令|内閣府令|命令)は[、,]?\s*"
    r"((?:明治|大正|昭和|平成|令和)(?:元|[0-9０-９一二三四五六七八九十百]+)"
    r"年[0-9０-９一二三四五六七八九十]+月(?:[0-9０-９一二三四五六七八九十]+日)?)"
    r"から施行"
)

RANGE_RE = re.compile(r"([0-9,]+)件中\s*([0-9,]+)件目\s*[～~]\s*([0-9,]+)件目")


def restore_inline_script_cells(target: Any, context: Mapping[str, Any] | None = None) -> str:
    """Evaluate simple ``<td><script>document.write(...)</script></td>`` cells into text or mutate DOM in-place."""
    if hasattr(target, "xpath"):
        for script in list(
            target.xpath(".//td/script | .//th/script | .//p/script | .//span/script")
        ):
            code = script.text_content() or ""
            m_var = re.search(
                r'var\s+([A-Za-z0-9_]+)\s*=\s*(["\'])(.*?)\2\s*;', code, re.DOTALL
            )
            if m_var and "document.write" in code:
                var_name, val = m_var.group(1), m_var.group(3)
                for m_rep in re.finditer(
                    rf"{re.escape(var_name)}\s*=\s*{re.escape(var_name)}\.replace\(\s*(['\"])(.*?)\1\s*,\s*(['\"])(.*?)\3\s*\)",
                    code,
                    re.DOTALL,
                ):
                    val = val.replace(m_rep.group(2), m_rep.group(4))
                val = re.sub(r"<br\s*/?>", "\n", val, flags=re.IGNORECASE)
                val = re.sub(r"<[^>]+>", "", val)
                parent = script.getparent()
                if parent is not None:
                    prev = script.getprevious()
                    if prev is not None:
                        prev.tail = (prev.tail or "") + val + (script.tail or "")
                    else:
                        parent.text = (parent.text or "") + val + (script.tail or "")
                    parent.remove(script)
        return ""

    text = str(target)
    if "<script" not in text.lower() or "document.write" not in text.lower():
        return text

    def _replace_cell(match: re.Match[str]) -> str:
        prefix, script_body, suffix = match.group(1), match.group(2), match.group(3)
        lit_m = _JS_DOC_WRITE_LIT_RE.search(script_body)
        if lit_m:
            val = lit_m.group(1) if lit_m.group(1) is not None else (lit_m.group(2) or "")
            return f"{prefix}{html_lib.escape(val)}{suffix}"
        var_m = _JS_DOC_WRITE_VAR_RE.search(script_body)
        if not var_m:
            return match.group(0)
        target_var = var_m.group(1)
        assign_val: str | None = None
        for assign_m in _JS_VAR_STRING_RE.finditer(script_body):
            if assign_m.group(1) == target_var:
                assign_val = (
                    assign_m.group(2)
                    if assign_m.group(2) is not None
                    else (assign_m.group(3) or "")
                )
                break
        if assign_val is None:
            return match.group(0)
        for rep_m in _JS_REPLACE_CALL_RE.finditer(script_body):
            old_s = rep_m.group(1) if rep_m.group(1) is not None else (rep_m.group(2) or "")
            new_s = rep_m.group(3) if rep_m.group(3) is not None else (rep_m.group(4) or "")
            assign_val = assign_val.replace(old_s, new_s, 1)
        return f"{prefix}{html_lib.escape(assign_val)}{suffix}"

    return _INLINE_SCRIPT_CELL_RE.sub(_replace_cell, text)


def gpo_xml_to_markdown(raw: bytes | str, context: Mapping[str, Any] | None = None) -> str:
    """Convert GPO bulk printing XML (HD/SECTNO/GPOTABLE/EXTRACT) into structured Markdown."""
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, recover=False, huge_tree=False
    )
    try:
        root: Any = etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError:
        raise WlhError("federal_register_xml_invalid") from None
    blocks: list[str] = []
    for elem in list(root.iter()):
        if not isinstance(elem.tag, str):
            continue
        tag = elem.tag.upper()
        if tag in ("HD", "HED", "SUBAGY", "AGY", "SUBJECT"):
            text = compact("".join(cast(list[str], elem.itertext())))
            if not text:
                continue
            source_attr = str(elem.get("SOURCE") or "").upper()
            level = "##"
            if source_attr.startswith("HD1") or tag in ("AGY", "SUBJECT"):
                level = "#"
            elif source_attr.startswith("HD2") or tag == "SUBAGY":
                level = "##"
            elif source_attr.startswith("HD3"):
                level = "###"
            blocks.append(f"{level} {text}")
        elif tag in ("P", "FP", "EXTRACT"):
            ancestors = {
                a.tag.upper()
                for a in list(elem.iterancestors())
                if isinstance(a.tag, str)
            }
            if tag in ("P", "FP") and "EXTRACT" in ancestors:
                continue
            text = compact("".join(cast(list[str], elem.itertext())))
            if text:
                blocks.append(text)
    if not blocks:
        fallback = compact("".join(cast(list[str], root.itertext())))
        if fallback:
            blocks.append(fallback)
    if not blocks:
        raise WlhError("federal_register_xml_empty")
    return "\n\n".join(blocks).strip() + "\n"


def external_figure_sources(root: Any) -> list[str]:
    """Return non-empty ``<Fig src="..."/>`` attributes inside an XML tree."""
    return [
        src
        for fig in cast(list[Any], root.xpath(".//*[local-name()='Fig']"))
        if (src := compact(str(fig.get("src") or "")))
        and not src.lower().startswith(("http://", "https://"))
    ]


def has_attached_files(payload: Mapping[str, Any]) -> bool:
    """Return True when ``attached_files_info`` declares downloadable files."""
    info = payload.get("attached_files_info")
    if not isinstance(info, Mapping):
        return False
    info_map = cast(Mapping[str, Any], info)
    files = info_map.get("attached_files")
    has_list = isinstance(files, list) and len(cast(list[Any], files)) > 0
    return bool(has_list or info_map.get("image_data"))


def verify_zip_attachments(
    root: Any,
    payload: Mapping[str, Any] | bytes | None = None,
    revision_id: str = "",
    origin: str = "",
) -> str | None:
    """Verify figure attachments in XML against attachment ZIP or API payload metadata."""
    figures = external_figure_sources(root)
    if isinstance(payload, (bytes, bytearray)) or payload is None:
        if not figures:
            return None
        if not payload:
            raise WlhError("law_figure_attachment_missing")
        import io
        import zipfile

        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                members = {
                    name.replace("\\", "/").lstrip("./")
                    for name in archive.namelist()
                    if not name.endswith("/")
                }
                basenames = {name.rsplit("/", 1)[-1] for name in members}
        except (zipfile.BadZipFile, OSError, ValueError):
            raise WlhError("law_figure_attachment_invalid") from None
        for src in figures:
            normalized_src = src.replace("\\", "/").lstrip("./")
            base_name = normalized_src.rsplit("/", 1)[-1]
            if normalized_src not in members and base_name not in basenames:
                raise WlhError("law_figure_attachment_incomplete")
        return "verified"

    has_files = has_attached_files(payload)
    if not figures and not has_files:
        return None
    if figures and not has_files:
        raise WlhError("egov_attachments_unverified")
    parsed = urlsplit(origin)
    base = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    return f"{base}/api/2/attachment/{revision_id}" if revision_id else "verified"


def commencement_effective_at(
    root: Any, context: Mapping[str, Any] | None = None
) -> str | None:
    """Extract effective date from unamended supplementary provision commencement clause."""
    for suppl in cast(
        list[Any], root.xpath(".//*[local-name()='SupplProvision'][not(@AmendLawNum)]")
    ):
        text = compact("".join(cast(list[str], suppl.itertext())))
        if not text:
            continue
        clauses = [c.strip() for c in re.split(r"[。.\n]+", text) if c.strip()]
        first_clause = clauses[0] if clauses else text
        match = _COMMENCEMENT_RE.search(first_clause)
        if match:
            date = parse_japanese_date(match.group(1))
            if date:
                return date
        date = parse_japanese_date(first_clause)
        if date and ("施行" in first_clause or "適用" in first_clause):
            return date
    return None


def reconcile_result_range(
    tree: Any, context: Mapping[str, Any] | None = None
) -> tuple[int, int, int]:
    """Parse ``M件中 N件目 ～ K件目`` from a search result page."""
    text = compact("".join(cast(list[str], tree.itertext())))
    match = RANGE_RE.search(text)
    if not match:
        raise WlhError("court_list_count_unparsed")
    total, start, end = (int(group.replace(",", "")) for group in match.groups())
    if total <= 0 or start <= 0 or end < start or end > total:
        raise WlhError("court_list_count_invalid")
    return total, start, end


def apply_moj_law_dates_and_meta(
    tree: Any,
    markdown: str,
    extracted_fields: dict[str, str | None],
    dates: dict[str, str | None],
    title: str,
    defaults: Mapping[str, Any],
) -> tuple[str, dict[str, str | None], dict[str, str | None]]:
    """Enrich legislative history dates, status, issuer, and bare titles."""
    body_text = compact("".join(cast(list[str], tree.itertext()))) if tree is not None else ""
    tw_meta = extract_taiwan_law_dates_and_meta(f"{body_text}\n{markdown}")
    for d_key in ("published_at", "effective_at", "expired_at"):
        if not dates.get(d_key) and tw_meta.get(d_key):
            dates[d_key] = tw_meta[d_key]
    if tw_meta.get("status") == "废止":
        extracted_fields["status"] = "废止"
    elif not extracted_fields.get("status") and tw_meta.get("status"):
        extracted_fields["status"] = tw_meta["status"]
    if not extracted_fields.get("issuer") and tw_meta.get("issuer"):
        extracted_fields["issuer"] = tw_meta["issuer"]

    pub_date, eff_date, exp_date = infer_missing_dates(
        tree,
        markdown,
        dates.get("published_at"),
        dates.get("effective_at"),
        dates.get("expired_at"),
        extracted_fields.get("document_number"),
    )
    dates["published_at"] = pub_date
    dates["effective_at"] = eff_date
    dates["expired_at"] = exp_date

    title = (
        enrich_bare_title(
            title,
            markdown,
            extracted_fields.get("document_number"),
            extracted_fields.get("issuer") or defaults.get("issuer"),
            dates.get("published_at"),
        )
        or title
    )
    return title, extracted_fields, dates


TRANSFORMS: dict[str, Callable[..., Any]] = {
    "inline_script_cell_restore": restore_inline_script_cells,
    "gpo_xml_to_markdown": gpo_xml_to_markdown,
    "verify_zip_attachments": verify_zip_attachments,
    "reconcile_result_range": reconcile_result_range,
    "commencement_clause_verify": commencement_effective_at,
    "moj_law_dates_and_meta": apply_moj_law_dates_and_meta,
}
TRANSFORM_REGISTRY = TRANSFORMS

assert set(TRANSFORMS) == set(PARSER_TRANSFORMS)

__all__ = [
    "RANGE_RE",
    "TRANSFORMS",
    "TRANSFORM_REGISTRY",
    "apply_moj_law_dates_and_meta",
    "commencement_effective_at",
    "external_figure_sources",
    "gpo_xml_to_markdown",
    "has_attached_files",
    "reconcile_result_range",
    "restore_inline_script_cells",
    "verify_zip_attachments",
]
