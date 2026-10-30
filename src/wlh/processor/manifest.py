"""Frozen parser semantics and deterministic identities, independent of persistence."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Iterable, Mapping
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from wlh.cleaners.extraction_quality import PRIMARY_TCVN3, QUALITY_CONTRACT
from wlh.core import WlhError, digest
from wlh.processor.extractors.html.html_to_md import MARKDOWN_CONTRACT
from wlh.processor.extractors.pdf.scan_detector import (
    INK_THRESHOLD,
    NORMAL_PAGE_TEXT_CHARS,
    PDF_CONTRACT,
    SCAN_INK_RATIO,
    SCAN_TEXT_CHARS,
    SIGNATURE_DOC_MAX_TEXT_CHARS,
    SIGNATURE_PAGE_MIN_COVERAGE,
    SIGNATURE_PAGE_MIN_INK,
)
from wlh.processor.extractors.quirks.vi_tcvn3 import TCVN3_CONTRACT
from wlh.processor.extractors.word.office import OFFICE_CONTRACT, libreoffice_identity

MANIFEST_ALGORITHM = "wlh.parser-manifest.v4"
FINGERPRINT_ALGORITHM = "wlh.fingerprint.v2"
ZIP_CONTRACT = "wlh.zip.v3"
ALWAYS = ("algorithm", "adapter", "fetch", "markdown")


def package(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "unavailable"


def ocr_runtime(languages: str) -> dict[str, Any]:
    executable = shutil.which(os.environ.get("WLH_TESSERACT", "tesseract"))
    if not executable:
        return {"status": "unavailable"}
    try:

        def probe(option: str) -> str:
            result: subprocess.CompletedProcess[bytes] = subprocess.run(
                [executable, option], capture_output=True, timeout=5, check=True
            )
            if len(result.stdout) > 65536:
                raise ValueError
            return result.stdout.decode("utf-8")

        engine = probe("--version").splitlines()[0]
        listing = probe("--list-langs")
        match: re.Match[str] | None = re.search(
            r'List of available languages in "([^"]+)"', listing
        )
        root = Path(os.environ.get("TESSDATA_PREFIX") or (match[1] if match else ""))
        models = {}
        for language in languages.split("+"):
            path = root / (language + ".traineddata")
            models[language] = digest(path.read_bytes()) if path.is_file() else "unavailable"
        return {
            "status": "available",
            "version": engine,
            "executable_sha256": digest(Path(executable).read_bytes()),
            "models": models,
        }
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return {"status": "unavailable"}


def manifest(config: Mapping[str, Any]) -> dict[str, Any]:
    from wlh.processor.parsers import (
        contract,
        effective_parser_options,
        normalize_parser_config,
        semantic_options,
    )

    norm = normalize_parser_config(config)
    fetch_cfg = norm["fetcher"]
    fetch: dict[str, Any] = {"mode": fetch_cfg["mode"]}
    if fetch["mode"] == "rendered_dom":
        from wlh.fetcher.browser_runtime import identity

        fetch.update(wait_until=fetch_cfg["wait_until"], runtime=identity())
    ocr = norm["ocr"]
    engine: dict[str, Any] = {"engine": ocr["engine"], "contract": "wlh.ocr.v1"}
    if ocr["engine"] == "tesseract":
        engine.update(
            languages=ocr["languages"], dpi=ocr["dpi"], runtime=ocr_runtime(ocr["languages"])
        )
    else:
        engine.update(
            model=ocr["model"],
            fallback={
                "engine": "tesseract",
                "languages": ocr["languages"],
                "dpi": ocr["dpi"],
                "runtime": ocr_runtime(ocr["languages"]),
            },
        )
    metadata = norm["metadata"]
    language = str(metadata.get("language") or "")
    adapter_name = str(norm["adapter"])
    adapter_opts = effective_parser_options(norm)
    documents = norm["documents"]
    transforms = list(norm.get("transforms") or [])
    quirks = list(norm.get("quirks") or [])
    cleaners_cfg = norm["cleaners"]
    cleaner_rules = {
        k: cleaners_cfg.get(k)
        for k in (
            "strip_text_regexes",
            "title_strip_regexes",
            "invalid_body_patterns",
            "infer_effective_date",
            "enrich_bare_title",
        )
    }
    office_policy = norm["parser"]["office_text_policy"]
    quality_routes = norm["parser"]["quality_accept_routes"]
    return {
        "algorithm": MANIFEST_ALGORITHM,
        "adapter": {
            "name": adapter_name,
            "contract": contract(adapter_name),
            "rules": digest(
                {
                    "adapter_options": semantic_options(adapter_name, adapter_opts),
                    "transforms": transforms,
                    "quirks": quirks,
                    "documents": documents,
                    "metadata": metadata,
                    "cleaners": cleaner_rules,
                }
            ),
        },
        "quirks": {"enabled": sorted(quirks)},
        "fetch": fetch,
        "markdown": {
            "contract": MARKDOWN_CONTRACT,
            "lxml": package("lxml"),
        },
        "office": {
            "contract": OFFICE_CONTRACT,
            "policy": office_policy,
            "python-docx": package("python-docx"),
            "openpyxl": package("openpyxl"),
            "xlrd": package("xlrd"),
            "libreoffice": libreoffice_identity(),
        },
        "tcvn3": {"contract": TCVN3_CONTRACT},
        "zip": {"contract": ZIP_CONTRACT},
        "pdf": {
            "contract": PDF_CONTRACT,
            "pymupdf": package("pymupdf"),
            "thresholds": {
                "native_text_chars_lt": SCAN_TEXT_CHARS,
                "ink_ratio_gt": SCAN_INK_RATIO,
                "ink_gray_lt": INK_THRESHOLD,
                "normal_page_text_chars": NORMAL_PAGE_TEXT_CHARS,
                "signature_doc_max_text_chars": SIGNATURE_DOC_MAX_TEXT_CHARS,
                "signature_page_min_coverage": SIGNATURE_PAGE_MIN_COVERAGE,
                "signature_page_min_ink": SIGNATURE_PAGE_MIN_INK,
            },
        },
        "ocr": engine,
        "quality": {
            "contract": QUALITY_CONTRACT,
            "accept_routes": quality_routes,
            "profile": "vi" if language.startswith("vi") else "generic",
            "primary_tcvn3": sorted(PRIMARY_TCVN3),
        },
    }


def require_runtime(config: Mapping[str, Any], frozen: Mapping[str, Any] | None) -> None:
    current = manifest(config)
    if frozen is None or frozen != current:
        raise WlhError("parser_runtime_changed_requires_new_run")
    if current["fetch"]["mode"] == "rendered_dom" and current["fetch"]["runtime"]["status"] != (
        "available"
    ):
        raise WlhError("browser_runtime_unavailable")


def effective(frozen: Mapping[str, Any] | None, used: Iterable[str]) -> dict[str, Any]:
    """The frozen manifest subset that actually produced this document."""
    if frozen is None:
        raise WlhError("parser_manifest_required")
    return {k: frozen[k] for k in frozen if k in ALWAYS or k in set(used)}


def fingerprint(kind: str, value: Any) -> str:
    return digest({"algorithm": FINGERPRINT_ALGORITHM, "kind": kind, "value": value})
