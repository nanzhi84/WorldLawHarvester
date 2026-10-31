from __future__ import annotations

import ast
import asyncio
import re
import socket
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from wlh.core import WlhError

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_WLH = REPO_ROOT / "src" / "wlh"

EXPECTED_PARSER_TYPES = ("html", "json", "xml")
EXPECTED_CHALLENGE_HOOKS = (
    "oracle_adf_loopback",
    "js_cookie_reload",
)
EXPECTED_PARSER_TRANSFORMS = (
    "inline_script_cell_restore",
    "gpo_xml_to_markdown",
    "verify_zip_attachments",
    "reconcile_result_range",
    "commencement_clause_verify",
    "moj_law_dates_and_meta",
)
EXPECTED_EXTRACTOR_QUIRKS = (
    "ja_kanpo_pua",
    "ja_inline_glyphs",
    "vi_tcvn3",
    "zip_salvage",
)

FORBIDDEN_OPERATOR_ERROR = (
    "Forbidden new operator added! You MUST use existing declarative YAML primitives in "
    "sources/dd-xxx.yaml (see sources/AGENTS.md for the 6 YAML Cookbook recipes: "
    "multi-XPath fallback, xpath+regex, label_value_rules, date cleaners, "
    "exclude_content_xpath, and JSON templates)."
)

FORBIDDEN_SITE_DOMAINS = (
    "v3.openstates.org",
    "federalregister.gov",
    "courts.go.jp",
    "laws.e-gov.go.jp",
    "nta.go.jp",
    "law.moj.gov.tw",
    "gazette.nat.gov.tw",
    "storage.courtlistener.com",
    "judicial.gov.tw",
    "vbpl.vn",
    "congbao.chinhphu.vn",
    "rechtsinformationen.bund.de",
    "entscheidsuche.ch",
)


def _all_forbidden_site_domains() -> set[str]:
    from wlh.config import load_file

    domains: set[str] = {str(d) for d in FORBIDDEN_SITE_DOMAINS}
    for src_yaml in (REPO_ROOT / "sources").glob("dd-*.yaml"):
        cfg = load_file(src_yaml)
        for host in cfg.scheduler.allowed_hosts:
            if "." in host:
                domains.add(host.lower())
    return domains


class TestArchitectureContract:
    def test_parser_package_layout_only_allows_three_universal_engines(self) -> None:
        for legacy_path in (
            SRC_WLH / "engine",
            SRC_WLH / "adapters",
            SRC_WLH / "extraction",
            SRC_WLH / "domain",
            SRC_WLH / "projections",
            SRC_WLH / "db.py",
        ):
            assert not legacy_path.exists(), f"Legacy path must be removed: {legacy_path}"

        for stage_dir in ("scheduler", "fetcher", "processor", "cleaners", "storage"):
            assert (SRC_WLH / stage_dir).is_dir(), f"Missing pipeline stage directory: {stage_dir}"

        parsers_dir = SRC_WLH / "processor" / "parsers"
        assert parsers_dir.is_dir()
        entries = {
            entry.name
            for entry in parsers_dir.iterdir()
            if entry.name != "__pycache__" and not entry.name.startswith(".")
        }
        expected_entries = {
            "__init__.py",
            "record_base.py",
            "transforms.py",
            "html",
            "json",
            "xml",
        }
        assert entries == expected_entries, (
            f"Unexpected files in processor/parsers/: {entries ^ expected_entries}. "
            f"{FORBIDDEN_OPERATOR_ERROR}"
        )

        xml_parsers_dir = parsers_dir / "xml"
        xml_parser_entries = {
            entry.name
            for entry in xml_parsers_dir.iterdir()
            if entry.name != "__pycache__" and not entry.name.startswith(".")
        }
        assert xml_parser_entries == {"__init__.py", "parser.py", "discovery.py", "values.py"}, (
            f"processor/parsers/xml/ must only contain navigation and metadata slicing modules, "
            f"not Markdown converters: {xml_parser_entries}"
        )

        extractors_dir = SRC_WLH / "processor" / "extractors"
        assert extractors_dir.is_dir()
        extractor_entries = {
            entry.name
            for entry in extractors_dir.iterdir()
            if entry.name != "__pycache__" and not entry.name.startswith(".")
        }
        expected_extractor_entries = {
            "__init__.py",
            "router.py",
            "slots.py",
            "extract_worker.py",
            "pdf",
            "word",
            "ocr",
            "html",
            "xml",
            "quirks",
        }
        assert extractor_entries == expected_extractor_entries, (
            f"processor/extractors/ must contain all 5 format extractor packages (pdf, word, ocr, html, xml) + quirks: "
            f"{extractor_entries ^ expected_extractor_entries}"
        )

        xml_extractors_dir = extractors_dir / "xml"
        xml_extractor_entries = {
            entry.name
            for entry in xml_extractors_dir.iterdir()
            if entry.name != "__pycache__" and not entry.name.startswith(".")
        }
        assert xml_extractor_entries == {"__init__.py", "markdown.py", "tables.py", "inline.py"}, (
            f"processor/extractors/xml/ must contain XML->Markdown converter modules: {xml_extractor_entries}"
        )

    def test_operator_registries_match_frozen_allowlists_and_atomic_signatures(self) -> None:
        from wlh import config
        from wlh.fetcher import challenges
        from wlh.processor.extractors import quirks
        from wlh.processor.parsers import PARSERS, transforms

        assert tuple(config.PARSER_TYPES) == EXPECTED_PARSER_TYPES
        assert set(PARSERS.keys()) == set(EXPECTED_PARSER_TYPES)
        assert tuple(config.CHALLENGE_HOOKS) == EXPECTED_CHALLENGE_HOOKS, FORBIDDEN_OPERATOR_ERROR
        assert tuple(config.PARSER_TRANSFORMS) == EXPECTED_PARSER_TRANSFORMS, (
            FORBIDDEN_OPERATOR_ERROR
        )
        assert tuple(config.EXTRACTOR_QUIRKS) == EXPECTED_EXTRACTOR_QUIRKS, FORBIDDEN_OPERATOR_ERROR

        assert set(challenges.CHALLENGES.keys()) == set(EXPECTED_CHALLENGE_HOOKS), (
            FORBIDDEN_OPERATOR_ERROR
        )
        assert set(transforms.TRANSFORMS.keys()) == set(EXPECTED_PARSER_TRANSFORMS), (
            FORBIDDEN_OPERATOR_ERROR
        )
        assert set(quirks.QUIRKS.keys()) == set(EXPECTED_EXTRACTOR_QUIRKS), FORBIDDEN_OPERATOR_ERROR

        for mod_path in (
            SRC_WLH / "fetcher" / "challenges.py",
            SRC_WLH / "processor" / "parsers" / "transforms.py",
            *(SRC_WLH / "processor" / "extractors" / "quirks").glob("*.py"),
        ):
            tree = ast.parse(mod_path.read_text(encoding="utf-8"), filename=str(mod_path))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
                    return_src = ast.unparse(node.returns)
                    forbidden_returns = ["RawRecord", "RawUnitSource", "RequestSpec"]
                    if mod_path.name == "transforms.py":
                        forbidden_returns.extend(["dict[", "Mapping["])
                    for forbidden_ret in forbidden_returns:
                        assert forbidden_ret not in return_src, (
                            f"Operator {node.name} in {mod_path} must not return {forbidden_ret}. "
                            f"{FORBIDDEN_OPERATOR_ERROR}"
                        )

    def test_pipeline_modules_contain_zero_site_hardcoding_and_respect_layer_boundaries(
        self,
    ) -> None:
        sources_agents = REPO_ROOT / "sources" / "AGENTS.md"
        assert sources_agents.is_file(), "sources/AGENTS.md must exist"
        agents_text = sources_agents.read_text(encoding="utf-8")
        assert "label_value_rules" in agents_text
        assert "exclude_content_xpath" in agents_text

        stage_dirs = ("scheduler", "fetcher", "processor", "cleaners", "storage")
        dd_pattern = re.compile(r"\bdd[-_]\d{3}\b", re.IGNORECASE)
        forbidden_domains = _all_forbidden_site_domains()

        for stage in stage_dirs:
            for py_file in (SRC_WLH / stage).rglob("*.py"):
                source_text = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source_text, filename=str(py_file))
                rel_parts = py_file.relative_to(SRC_WLH).parts

                for node in ast.walk(tree):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str):
                        val = node.value
                        val_lower = val.lower()
                        for domain in forbidden_domains:
                            assert domain not in val_lower, (
                                f"Site domain '{domain}' hardcoded in {py_file}:{node.lineno}"
                            )
                        assert not dd_pattern.search(val), (
                            f"Source ID pattern hardcoded in {py_file}:{node.lineno}: {val!r}"
                        )
                        if stage in ("processor", "cleaners"):
                            assert not re.search(
                                r"\bSELECT\b.+\bFROM\s+wlh\.", val, re.IGNORECASE | re.DOTALL
                            ), f"Direct SQL query found in {stage} module {py_file}:{node.lineno}"

                    if stage in ("processor", "cleaners") and isinstance(
                        node, (ast.Import, ast.ImportFrom)
                    ):
                        imported_modules: list[str] = []
                        if isinstance(node, ast.Import):
                            imported_modules.extend(alias.name for alias in node.names)
                        elif node.module:
                            imported_modules.append(node.module)

                        for mod_name in imported_modules:
                            assert not mod_name.startswith(
                                ("psycopg", "wlh.storage.db", "wlh.db")
                            ), f"Forbidden DB import '{mod_name}' in {py_file}:{node.lineno}"
                            if rel_parts[:2] == ("processor", "extractors"):
                                assert not mod_name.startswith("wlh.processor.parsers"), (
                                    f"processor/extractors/ must never reverse-import from processor/parsers/ "
                                    f"('{mod_name}' in {py_file}:{node.lineno})"
                                )
                            if stage == "cleaners":
                                assert not mod_name.startswith("wlh.processor"), (
                                    f"cleaners/ must never reverse-import from processor/ "
                                    f"('{mod_name}' in {py_file}:{node.lineno})"
                                )

                    if (
                        isinstance(node, ast.FunctionDef)
                        and node.name.endswith("_to_markdown")
                        and rel_parts[:2] != ("processor", "extractors")
                    ):
                        raise AssertionError(
                            f"Markdown conversion function '{node.name}' must live in "
                            f"src/wlh/processor/extractors/, found in {py_file}:{node.lineno}"
                        )

    def test_xml_extractor_converts_legal_and_gpo_xml_to_standard_markdown(self) -> None:
        import lxml.etree as _etree  # type: ignore[import-untyped]

        from wlh.processor.extractors.xml import (
            dokument_xml_to_markdown,
            egov_law_xml_to_markdown,
            gazette_html_to_markdown,
            gpo_xml_to_markdown,
            inline_content,
            markdown,
            table_markdown,
            xml_to_markdown,
        )

        etree: Any = _etree
        law_xml_bytes = (
            b"<Law><LawBody><LawTitle>Environmental Act</LawTitle>"
            b"<Article><ArticleCaption>Purpose</ArticleCaption><ArticleTitle>Article 1</ArticleTitle>"
            b"<Paragraph><Sentence>This Act applies to <Ruby>law<Rt>hou</Rt></Ruby> and CO<Sub>2</Sub>.</Sentence></Paragraph>"
            b"</Article>"
            b"<TableStruct><Table><TableRow>"
            b"<TableColumn BorderTop='solid' BorderBottom='none'>Category A</TableColumn>"
            b"<TableColumn colspan='2'>Scope 1 &amp; 2</TableColumn>"
            b"</TableRow></Table></TableStruct>"
            b"</LawBody></Law>"
        )
        law_root: Any = etree.fromstring(law_xml_bytes)
        law_md = markdown(law_root, title="Environmental Act")
        assert law_md.startswith("# Environmental Act\n\n")
        assert "## Environmental Act" in law_md
        assert "## Purpose" in law_md
        assert "Article 1" in law_md
        assert "<ruby>law<rt>hou</rt></ruby>" in law_md
        assert "CO<sub>2</sub>" in law_md
        assert 'colspan="2"' in law_md
        assert "border-top:1px solid currentColor;border-bottom:none" in law_md

        # xml_to_markdown must also convert non-GPO standard Legal XML and <dokument> XML
        law_bytes_md = xml_to_markdown(law_xml_bytes)
        assert "## Environmental Act" in law_bytes_md
        assert "<ruby>law<rt>hou</rt></ruby>" in law_bytes_md

        egov_root: Any = etree.fromstring(
            b"<Law><LawBody><LawTitle>Water Pollution Prevention Act</LawTitle>"
            b"<Article><ArticleTitle>Article 1</ArticleTitle>"
            b"<Paragraph><Sentence>Standards are shown in <Fig src='./pict/table1.pdf'/>.</Sentence></Paragraph>"
            b"</Article></LawBody></Law>"
        )
        egov_md = egov_law_xml_to_markdown(
            egov_root,
            title="Water Pollution Prevention Act",
            number="Act No. 138 of 1970",
            enforcement_comment="Enforced within 6 months",
            scheduled_enforcement_date="2026-04-01",
            att_srcs=("pict/table1.pdf", "pict/annex2.pdf"),
        )
        assert egov_md.startswith("# Water Pollution Prevention Act\n\nAct No. 138 of 1970")
        assert "施行期日参考情報：Enforced within 6 months" in egov_md
        assert "施行期日参考情報（予定）：2026-04-01" in egov_md
        assert "[添付ファイル: pict/table1.pdf]" in egov_md
        assert "[添付ファイル: pict/annex2.pdf]" in egov_md

        tbl_node: Any = law_root.find(".//Table")
        assert tbl_node is not None
        tbl_md = table_markdown(tbl_node)
        assert tbl_md is not None and "<table" in tbl_md
        sent_node: Any = law_root.find(".//Sentence")
        assert sent_node is not None
        assert "<sub>2</sub>" in inline_content(sent_node)

        gpo_raw = (
            b"<RULE><PREAMB><AGY>ENVIRONMENTAL PROTECTION AGENCY</AGY>"
            b"<HD SOURCE='HD1'>Supplementary Information</HD>"
            b"<P>This final rule establishes national emission standards for hazardous air pollutants.</P>"
            b"</PREAMB></RULE>"
        )
        assert "## Supplementary Information" in xml_to_markdown(gpo_raw)
        assert "# Supplementary Information" in gpo_xml_to_markdown(gpo_raw)

        dok_bytes = (
            b"<dokument><leitsatz>Guiding principle on environmental liability.</leitsatz>"
            b"<tenor>The appeal is dismissed.</tenor></dokument>"
        )
        dok_root: Any = etree.fromstring(dok_bytes)
        assert (
            dokument_xml_to_markdown(dok_root)
            == "Guiding principle on environmental liability.\n\nThe appeal is dismissed."
        )
        assert "Guiding principle on environmental liability." in xml_to_markdown(dok_bytes)
        gaz_md = gazette_html_to_markdown(
            "<p>Gazette body</p>", "https://example.gov", title="Gazette Notice"
        )
        assert gaz_md.startswith("# Gazette Notice\n\n") and "Gazette body" in gaz_md


class TestSourceConfigContract:
    def test_five_stage_source_config_accepts_valid_universal_parsers_and_hooks(self) -> None:
        from wlh.config import SourceConfig

        cfg = SourceConfig.model_validate(
            {
                "id": "dd-999",
                "scheduler": {
                    "scope": "Test scope for five-stage config validation",
                    "entry_urls": ["https://example.gov/laws/index.html"],
                    "allowed_hosts": ["example.gov"],
                    "allowed_path_prefixes": ["/laws"],
                    "url_rewrites": [
                        {
                            "pattern": r"^https://example\.gov/hot/jump\?id=(\d+)$",
                            "replacement": r"https://example.gov/laws/\1.html",
                        }
                    ],
                    "max_depth": 2,
                    "concurrency": 2,
                    "recheck_seconds": 3600,
                    "budget": {"requests": 50, "documents": 20, "max_pages": 5},
                },
                "fetcher": {
                    "mode": "http",
                    "headers_from_env": {"X-API-KEY": "TEST_API_KEY"},
                    "pagination_click_selector": "a.next-btn",
                    "challenges": ["oracle_adf_loopback"],
                    "proxy": {"policy": "auto", "profile": "tw"},
                },
                "processor": {
                    "parser": {
                        "type": "html",
                        "transforms": ["inline_script_cell_restore"],
                        "documents": {
                            "detail_url_patterns": [r"/laws/\d+\.html$"],
                            "exclude_detail_url_patterns": [r"/laws/0+\.html$"],
                            "content_xpath": "//div[@id='content']",
                            "exclude_content_xpath": [".//div[@class='share']"],
                            "label_value_rules": [
                                {
                                    "row_xpath": "//table[@class='meta']//tr",
                                    "label_xpath": "./th",
                                    "value_xpath": "./td",
                                    "mapping": {"发文日期": "issued_date", "文号": "doc_num"},
                                }
                            ],
                        },
                    },
                    "extractor": {
                        "quirks": ["ja_kanpo_pua", "vi_tcvn3"],
                    },
                },
                "cleaners": {
                    "require_complete_text": True,
                    "allow_single_resource": True,
                    "infer_effective_date": True,
                    "enrich_bare_title": True,
                    "strip_text_regexes": [r"【字体：大 中 小】"],
                    "title_strip_regexes": [r"\s*-\s*示例网站$"],
                    "invalid_body_patterns": [r"请登录查看全文"],
                },
                "storage": {
                    "archive_approved": True,
                    "metadata": {
                        "source_name": "示例法规库",
                        "document_type": "法律法规",
                    },
                },
            }
        )
        assert cfg.processor.parser.type == "html"
        assert cfg.fetcher.proxy.profile == "tw"
        assert cfg.cleaners.allow_single_resource is True

    def test_source_config_rejects_legacy_flat_keys_and_unapproved_operators(self) -> None:
        from wlh.config import SourceConfig

        with pytest.raises(Exception):
            SourceConfig.model_validate(
                {
                    "id": "dd-999",
                    "adapter": "web",
                    "entry_urls": ["https://example.gov/"],
                    "allowed_hosts": ["example.gov"],
                    "scope": "Legacy flat config",
                    "archive_approved": True,
                }
            )

        base_valid: dict[str, Any] = {
            "id": "dd-999",
            "scheduler": {
                "scope": "Valid scope",
                "entry_urls": ["https://example.gov/"],
                "allowed_hosts": ["example.gov"],
            },
            "storage": {"archive_approved": True},
        }

        with pytest.raises(Exception):
            SourceConfig.model_validate(
                {
                    **base_valid,
                    "processor": {"parser": {"type": "web"}},
                }
            )

        with pytest.raises(Exception):
            SourceConfig.model_validate(
                {
                    **base_valid,
                    "processor": {"parser": {"type": "html", "transforms": ["custom_site_hook"]}},
                }
            )

        with pytest.raises(Exception):
            SourceConfig.model_validate(
                {
                    **base_valid,
                    "processor": {"extractor": {"quirks": ["unknown_quirk"]}},
                }
            )


class TestDeclarativePipelineHooks:
    def test_url_rewrites_and_exclude_detail_patterns_filter_and_normalize_targets(self) -> None:
        from wlh.scheduler.scope import apply_url_rewrites, guard

        cfg = {
            "scheduler": {
                "entry_urls": ["https://example.gov/laws/list"],
                "allowed_hosts": ["example.gov"],
                "allowed_path_prefixes": ["/laws"],
                "url_rewrites": [
                    {
                        "pattern": r"^https://example\.gov/hot/add\?PCode=([A-Z0-9]+)$",
                        "replacement": r"https://example.gov/laws/detail?PCode=\1",
                    }
                ],
                "max_depth": 3,
            }
        }
        rewritten = apply_url_rewrites(
            "https://example.gov/hot/add?PCode=A001",
            cfg,
        )
        assert rewritten == "https://example.gov/laws/detail?PCode=A001"
        assert guard(cfg, rewritten, depth=1) == "https://example.gov/laws/detail?PCode=A001"

    def test_html_parser_label_value_rules_and_dom_cleanup_extract_clean_records(self) -> None:
        from wlh.cleaners.dates import infer_effective_date, parse_date
        from wlh.cleaners.text_cleaner import apply_text_cleaners, clean_title
        from wlh.processor.extractors.html.dom_tree import document_tree
        from wlh.processor.parsers.html.dom_cleanup import clean_site_dom
        from wlh.processor.parsers.html.fields import extract_label_value_fields

        raw_html = """
        <html><body>
          <div id="content">
            <dl class="meta-list">
              <div><dt>裁判年月日</dt><dd>令和6年5月1日</dd></div>
              <div><dt>事件番号</dt><dd>令和5(オ)第123号</dd></div>
              <div><dt>裁判所名</dt><dd>最高裁判所第一小法廷</dd></div>
            </dl>
            <div class="law-body">
              <p>第一条 本法自公布日施行。</p>
              <div class="share-bar">分享到微博</div>
            </div>
          </div>
        </body></html>
        """
        tree: Any = document_tree(raw_html)
        extracted = extract_label_value_fields(
            tree,
            [
                {
                    "row_xpath": "//dl[@class='meta-list']/div",
                    "label_xpath": "./dt",
                    "value_xpath": "./dd",
                    "mapping": {
                        "裁判年月日": "issued_date",
                        "事件番号": "doc_num",
                        "裁判所名": "issuer",
                    },
                }
            ],
        )
        assert parse_date(extracted["issued_date"]) == "2024-05-01"
        assert extracted["doc_num"] == "令和5(オ)第123号"
        assert extracted["issuer"] == "最高裁判所第一小法廷"

        content_node: Any = tree.xpath("//div[@class='law-body']")[0]
        clean_site_dom(content_node, [".//div[@class='share-bar']"])
        text = "".join(str(t) for t in content_node.itertext())
        assert "分享到微博" not in text

        cleaned_title = clean_title("示例法（檔案下載） - 全国法规库", [r"（檔案下載）", r"\s*-\s*全国法规库$"])
        assert cleaned_title == "示例法"

        cleaned_body = apply_text_cleaners("第一条 本法自公布日施行。【字体：大 中 小】", [r"【字体：大 中 小】"])
        assert "【字体" not in cleaned_body
        assert infer_effective_date(cleaned_body, published_at="2024-05-01") == "2024-05-01"

    def test_cleaners_rules_and_manifest_fingerprint_sensitivity(self) -> None:
        from wlh.cleaners.extraction_quality import text_check
        from wlh.cleaners.text_cleaner import blocked
        from wlh.processor.extractors.html.dom_tree import parse_html
        from wlh.processor.manifest import manifest
        from wlh.processor.parsers.html.document_builder import WebDocumentBuilder

        base_cfg: dict[str, Any] = {
            "id": "dd-998",
            "scheduler": {
                "scope": "Test scope for cleaner rules and manifest fingerprint",
                "entry_urls": ["https://example.gov/laws/1.html"],
                "allowed_hosts": ["example.gov"],
            },
            "processor": {
                "parser": {
                    "type": "html",
                    "documents": {
                        "content_xpath": "//div[@id='body']",
                        "fields": {"title": {"xpath": "//h1/text()"}},
                    },
                }
            },
            "cleaners": {
                "strip_text_regexes": [r"\[责任编辑：[^\]]+\]"],
                "title_strip_regexes": [r"\s*\|\s*官方法规库$"],
                "invalid_body_patterns": [r"请登录VIP账号查看全文"],
            },
            "storage": {"archive_approved": True, "metadata": {"document_type": "法律法规"}},
        }
        builder = WebDocumentBuilder(base_cfg)
        html_bytes = (
            "<html><body><h1>税法实施条例 | 官方法规库</h1>"
            "<div id='body'><p>第一条 纳税人应当依法纳税。[责任编辑：张三]</p></div>"
            "</body></html>"
        ).encode("utf-8")
        acq = {
            "actual_url": "https://example.gov/laws/1.html",
            "request_url": "https://example.gov/laws/1.html",
            "charset": "utf-8",
        }
        tree, _, _ = parse_html(html_bytes, "utf-8")
        md = builder.html_markdown(tree, acq)
        assert "第一条 纳税人应当依法纳税。" in md
        assert "责任编辑" not in md
        _vals, title, _enc = builder.detail_values(
            html_bytes, "html", acq, "https://example.gov/laws/1.html"
        )
        assert title == "税法实施条例"

        blocked_html = b"<html><body><div id='body'>\xe8\xaf\xb7\xe7\x99\xbb\xe5\xbd\x95VIP\xe8\xb4\xa6\xe5\x8f\xb7\xe6\x9f\xa5\xe7\x9c\x8b\xe5\x85\xa8\xe6\x96\x87</div></body></html>"
        assert blocked(blocked_html, base_cfg) is True
        q_report = text_check(
            "请登录VIP账号查看全文",
            invalid_body_patterns=base_cfg["cleaners"]["invalid_body_patterns"],
        )
        assert "blocked_or_placeholder_text" in q_report["document_reasons"]

        m1 = manifest(base_cfg)
        cfg_changed_cleaners: dict[str, Any] = {
            **base_cfg,
            "cleaners": {**base_cfg["cleaners"], "strip_text_regexes": []},
        }
        m2 = manifest(cfg_changed_cleaners)
        assert m1["adapter"]["rules"] != m2["adapter"]["rules"]

        cfg_changed_transforms: dict[str, Any] = {
            **base_cfg,
            "processor": {
                "parser": {
                    **base_cfg["processor"]["parser"],
                    "transforms": ["inline_script_cell_restore"],
                }
            },
        }
        m3 = manifest(cfg_changed_transforms)
        assert m1["adapter"]["rules"] != m3["adapter"]["rules"]

    def test_ja_inline_glyphs_does_not_swallow_non_glyph_parts_image(self) -> None:
        from wlh.processor.extractors.html.dom_tree import parse_html
        from wlh.processor.extractors.quirks.ja_kanpo_pua import inline_glyph_text
        from wlh.processor.parsers.html.document_builder import WebDocumentBuilder

        table_html = (
            "<html><body><div id='body'>"
            "<p>別表のとおり<img src='/parts/tax-table.gif' alt='別表1'></p>"
            "</div></body></html>"
        ).encode("utf-8")
        tree, _, _ = parse_html(table_html, "utf-8")
        img_node = tree.xpath("//img")[0]
        assert inline_glyph_text(img_node) is None

        cfg: dict[str, Any] = {
            "id": "dd-997",
            "scheduler": {
                "scope": "Test scope for unverified image under /parts/",
                "entry_urls": ["https://example.gov/laws/1.html"],
                "allowed_hosts": ["example.gov"],
            },
            "processor": {
                "parser": {
                    "type": "html",
                    "documents": {"content_xpath": "//div[@id='body']"},
                },
                "extractor": {"quirks": ["ja_inline_glyphs"]},
            },
            "cleaners": {"require_complete_text": True},
            "storage": {"archive_approved": True, "metadata": {"document_type": "法律法规"}},
        }
        builder = WebDocumentBuilder(cfg)
        with pytest.raises(WlhError, match="html_images_unverified"):
            builder.html_markdown(
                tree,
                {
                    "actual_url": "https://example.gov/laws/1.html",
                    "request_url": "https://example.gov/laws/1.html",
                    "charset": "utf-8",
                },
            )

    def test_json_parser_accepts_and_applies_declarative_recipe_options(self) -> None:
        from wlh.config import SourceConfig
        from wlh.processor.parsers.json.parser import JsonParser

        raw_cfg: dict[str, Any] = {
            "id": "dd-996",
            "scheduler": {
                "scope": "Declarative JSON API Recipe 6 test",
                "entry_urls": ["https://example.gov/api/v1/laws?page=1"],
                "allowed_hosts": ["example.gov"],
                "allowed_path_prefixes": ["/api/"],
            },
            "processor": {
                "parser": {
                    "type": "json",
                    "options": {
                        "max_pages": 3,
                        "items_path": "data.items",
                        "pagination": {
                            "type": "page_param",
                            "param": "page",
                            "max_pages_path": "data.pagination.max_page",
                        },
                        "detail_url_template": "https://example.gov/api/v1/laws/{id}",
                        "attachment_rules": [
                            {"url_path": "files.pdf_url", "title_path": "title"}
                        ],
                    },
                }
            },
            "storage": {"archive_approved": True, "metadata": {"document_type": "法律法规"}},
        }
        validated = SourceConfig.model_validate(raw_cfg)
        parser = JsonParser(validated.model_dump())
        payload_bytes = b"""{
            "data": {
                "pagination": {"max_page": 2},
                "items": [
                    {
                        "id": "law-101",
                        "title": " Clean Air Act ",
                        "text": "Article 1. Clean air provisions apply nationwide.",
                        "files": {"pdf_url": "https://example.gov/api/files/law-101.pdf"}
                    }
                ]
            }
        }"""
        acq = {
            "actual_url": "https://example.gov/api/v1/laws?page=1",
            "request_url": "https://example.gov/api/v1/laws?page=1",
            "charset": "utf-8",
        }
        recs = parser.records(payload_bytes, acq)
        assert len(recs) == 1
        assert recs[0]["external_key"] == "law-101"
        assert recs[0]["detail_url"] == "https://example.gov/api/v1/laws/law-101"
        assert recs[0]["downloads"] == [
            {"url": "https://example.gov/api/files/law-101.pdf", "title": " Clean Air Act "}
        ]
        next_reqs = parser.next_requests(payload_bytes, acq)
        assert any(r["url"] == "https://example.gov/api/v1/laws?page=2" for r in next_reqs)


class TestEphemeralProxyManager:
    def test_direct_and_unconfigured_auto_policies_skip_proxy_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from wlh.fetcher.proxy import EphemeralProxy

        monkeypatch.delenv("WLH_PROXY_URL", raising=False)
        monkeypatch.delenv("WLH_PROXY_CMD_TEMPLATE", raising=False)

        async def _run() -> None:
            async with EphemeralProxy(policy="direct") as url_direct:
                assert url_direct is None
            async with EphemeralProxy(policy="auto", profile="tw") as url_auto:
                assert url_auto is None

        asyncio.run(_run())

    def test_required_policy_without_configuration_raises_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from wlh.fetcher.proxy import EphemeralProxy
        from wlh.fetcher.transport import Budget, Recorder, Transport

        monkeypatch.delenv("WLH_PROXY_URL", raising=False)
        monkeypatch.delenv("WLH_PROXY_NONEXISTENT", raising=False)
        monkeypatch.delenv("WLH_PROXY_CMD_NONEXISTENT", raising=False)
        monkeypatch.delenv("WLH_PROXY_CMD_TEMPLATE", raising=False)

        async def _run() -> None:
            with pytest.raises(WlhError, match="proxy_required_but_unconfigured"):
                async with EphemeralProxy(policy="required", profile="nonexistent"):
                    pass
            t_cfg: dict[str, Any] = {
                "fetcher": {
                    "mode": "http",
                    "timeout_ms": 5000,
                    "max_response_bytes": 1024 * 1024,
                    "proxy": {"policy": "required", "profile": "nonexistent"},
                },
                "scheduler": {"allowed_hosts": ["example.gov"], "allowed_path_prefixes": ["/"]},
            }
            transport = Transport(
                t_cfg,
                budget=Budget(t_cfg, used=0, deadline=None),
                recorder=Recorder("run-1", 1, "dd-999"),
            )
            with pytest.raises(WlhError, match="proxy_required_but_unconfigured"):
                await transport.start()
            with pytest.raises(WlhError, match="proxy_required_but_unconfigured"):
                await transport.start()

        asyncio.run(_run())

    def test_ephemeral_proxy_spawns_on_random_free_port_and_terminates_on_exit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from wlh.fetcher.network import Resolver
        from wlh.fetcher.proxy import EphemeralProxy

        monkeypatch.setenv("WLH_ALLOW_LOOPBACK", "0")
        cmd = (
            f"{sys.executable} -c "
            "\"import socket, time, sys; "
            "s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); "
            "s.bind(('127.0.0.1', int(sys.argv[1]))); s.listen(5); "
            "time.sleep(60)\" {port}"
        )
        monkeypatch.setenv("WLH_PROXY_CMD_TEST", cmd)

        async def _run() -> None:
            proxy_mgr = EphemeralProxy(policy="required", profile="test")
            allocated_port: int | None = None
            async with proxy_mgr as proxy_url:
                assert proxy_url is not None
                parts = urlsplit(proxy_url)
                assert parts.hostname == "127.0.0.1"
                assert parts.port is not None
                assert parts.port != 7890
                allocated_port = parts.port
                assert proxy_mgr.url == proxy_url
                assert "127.0.0.1" in proxy_mgr.trusted_hosts

                _reader, writer = await asyncio.open_connection("127.0.0.1", allocated_port)
                writer.close()
                await writer.wait_closed()

                resolver = Resolver(trusted_proxy_hosts=proxy_mgr.trusted_hosts)
                resolved = await resolver.resolve("127.0.0.1", allocated_port)
                assert resolved[0]["host"] == "127.0.0.1"

                with pytest.raises(WlhError):
                    await resolver.resolve("localhost", 80)

            assert allocated_port is not None
            await asyncio.sleep(0.05)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.2)
                assert sock.connect_ex(("127.0.0.1", allocated_port)) != 0

        asyncio.run(_run())

    def test_ephemeral_proxy_startup_retries_and_transport_async_context_manager(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from wlh.config import load_file
        from wlh.fetcher.proxy import EphemeralProxy
        from wlh.fetcher.transport import Budget, Recorder, Transport
        from wlh.processor.parsers.html.parser import HtmlParser

        # 1. Verify examples/source.yaml validates under 5-stage SourceConfig
        example_cfg = load_file(REPO_ROOT / "examples" / "source.yaml")
        assert example_cfg.id == "dd-499"
        assert example_cfg.processor.parser.type == "json"

        # 2. Verify html_to_md.py and html/parser.py contain no site-specific selector literals
        html_to_md_text = (SRC_WLH / "processor" / "extractors" / "html" / "html_to_md.py").read_text(
            encoding="utf-8"
        )
        for forbidden_literal in ("_sblb", "hmc4Table", "xgxw", "gem-c-print-link", '"dataset"'):
            assert forbidden_literal not in html_to_md_text

        html_parser_text = (SRC_WLH / "processor" / "parsers" / "html" / "parser.py").read_text(
            encoding="utf-8"
        )
        for forbidden_xpath in ("iframe-data", "hlNext", "vblienquan", "Side_Links"):
            assert forbidden_xpath not in html_parser_text

        # 3. Verify HtmlParser handles attachment_exclude_xpath as list[str] and next_page_xpath
        h_cfg: dict[str, Any] = {
            "id": "dd-994",
            "scheduler": {
                "scope": "Test attachment_exclude_xpath list and next_page_xpath",
                "entry_urls": ["https://example.gov/list.html"],
                "allowed_hosts": ["example.gov"],
                "max_depth": 2,
            },
            "processor": {
                "parser": {
                    "type": "html",
                    "options": {"max_pages": 3},
                    "documents": {
                        "detail_url_patterns": [r"^https://example\.gov/detail/\d+\.html$"],
                        "next_page_xpath": "//a[@class='next-page-link' and @href]",
                        "attachment_exclude_xpath": [
                            "ancestor::div[@id='sidebar-related']",
                            "ancestor::aside[@class='ignored-files']",
                        ],
                        "attachment_title_xpath": "preceding-sibling::span[@class='att-name'][1]",
                    },
                }
            },
            "storage": {"archive_approved": True, "metadata": {"document_type": "法律法规"}},
        }
        hp = HtmlParser(h_cfg)

        class _StubRuntime:
            mode = "collect"

        class _StubCtx:
            runtime = _StubRuntime()

            @staticmethod
            def read(_acq: Mapping[str, Any]) -> bytes:
                return (
                    b"<html><body>"
                    b"<div class='main'>"
                    b"<span class='att-name'>Official Tax Schedule</span>"
                    b"<a href='/files/valid.pdf'>PDF</a>"
                    b"</div>"
                    b"<div id='sidebar-related'><a href='/files/excluded.pdf'>PDF</a></div>"
                    b"</body></html>"
                )

            @staticmethod
            def follow_links(_task: Mapping[str, Any]) -> bool:
                return True

        parsed_detail = hp.parse(
            _StubCtx(),
            {"depth": 1, "payload": {"url": "https://example.gov/detail/1.html", "role": "page"}},
            {
                "id": "acq-1",
                "outcome": "ok",
                "actual_url": "https://example.gov/detail/1.html",
                "request_url": "https://example.gov/detail/1.html",
                "mime_type": "text/html",
                "charset": "utf-8",
            },
        )
        assert len(parsed_detail["targets"]) == 1
        target_inputs = parsed_detail["targets"][0]["inputs"]
        att_inputs = [i for i in target_inputs if i["role"] == "attachment"]
        assert len(att_inputs) == 1
        assert att_inputs[0]["request"]["url"] == "https://example.gov/files/valid.pdf"
        assert att_inputs[0]["text"] == "Official Tax Schedule"

        # 4. Verify EphemeralProxy retries 3 times on startup failure and raises ephemeral_proxy_startup_failed,
        # and Transport supports async with (__aenter__ / __aexit__)
        counter_file = tmp_path / "attempts.txt"
        fail_cmd = (
            f"{sys.executable} -c "
            f"\"from pathlib import Path; p = Path({str(counter_file)!r}); "
            "n = int(p.read_text()) if p.exists() else 0; "
            "p.write_text(str(n + 1)); raise SystemExit(1)\" {port}"
        )
        monkeypatch.setenv("WLH_PROXY_CMD_FAILTEST", fail_cmd)

        async def _run_fail_and_ctx() -> None:
            with pytest.raises(WlhError, match="ephemeral_proxy_startup_failed"):
                async with EphemeralProxy(
                    {"policy": "required", "profile": "failtest", "startup_timeout_seconds": 1}
                ):
                    pass
            assert int(counter_file.read_text()) == 3

            t_cfg: dict[str, Any] = {
                "fetcher": {"mode": "http", "proxy": {"policy": "direct"}},
                "scheduler": {"allowed_hosts": ["example.gov"], "allowed_path_prefixes": ["/"]},
            }
            transport = Transport(
                t_cfg,
                budget=Budget(t_cfg, used=0, deadline=None),
                recorder=Recorder("run-ctx", 1, "dd-999"),
            )
            async with transport as entered:
                assert entered is transport
                assert transport.session is not None
            assert transport.session is None

        asyncio.run(_run_fail_and_ctx())
