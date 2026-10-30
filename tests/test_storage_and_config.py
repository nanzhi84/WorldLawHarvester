"""Public source configuration and registry alignment contracts."""

from __future__ import annotations

from pathlib import Path

import pytest


class TestSourceConfigValidation:
    def test_approved_in_scope_config_loads_successfully(self, tmp_path: Path) -> None:
        """A source configuration with archive approval and in-scope entry URLs loads cleanly."""
        from wlh.config import PARSER_TYPES, load_file

        required_parsers = {"html", "json", "xml"}
        assert set(PARSER_TYPES) == required_parsers

        valid_yaml = tmp_path / "valid.yaml"
        valid_yaml.write_text(
            """
id: dd-499
scheduler:
  scope: "Federal Register API test scope"
  entry_urls:
    - "https://www.federalregister.gov/api/v1/documents.json?per_page=2"
  allowed_hosts:
    - "www.federalregister.gov"
  allowed_path_prefixes:
    - "/api/v1/"
fetcher:
  mode: http
processor:
  parser:
    type: json
cleaners: {}
storage:
  archive_approved: true
  metadata:
    source_name: "Federal Register"
    document_type: "法律法规"
    language: "en"
    jurisdiction: "US"
""".strip(),
            encoding="utf-8",
        )
        cfg = load_file(valid_yaml)
        assert cfg.id == "dd-499"
        assert cfg.processor.parser.type == "json"

    def test_unapproved_or_out_of_scope_or_loopback_config_raises_validation_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Configs missing archive approval, violating host/path scope, or targeting loopback are rejected."""
        from wlh.config import load_file
        from wlh.core import WlhError

        base_yaml = """
id: dd-499
scheduler:
  scope: "Federal Register API test scope"
  entry_urls:
    - "https://www.federalregister.gov/api/v1/documents.json?per_page=2"
  allowed_hosts:
    - "www.federalregister.gov"
  allowed_path_prefixes:
    - "/api/v1/"
fetcher:
  mode: http
processor:
  parser:
    type: json
cleaners: {}
storage:
  archive_approved: true
  metadata:
    source_name: "Federal Register"
    document_type: "法律法规"
    language: "en"
    jurisdiction: "US"
""".strip()

        unapproved_yaml = tmp_path / "unapproved.yaml"
        unapproved_yaml.write_text(
            base_yaml.replace("archive_approved: true", "archive_approved: false"),
            encoding="utf-8",
        )
        with pytest.raises(WlhError) as exc_unapproved:
            load_file(unapproved_yaml)
        assert exc_unapproved.value.code in {
            "source_archive_approval_required",
            "invalid_source_config",
        }

        out_of_scope_yaml = tmp_path / "out_of_scope.yaml"
        out_of_scope_yaml.write_text(
            base_yaml.replace(
                "https://www.federalregister.gov/api/v1/documents.json?per_page=2",
                "https://evil.example.com/api/v1/documents.json",
            ),
            encoding="utf-8",
        )
        with pytest.raises(WlhError) as exc_scope:
            load_file(out_of_scope_yaml)
        assert exc_scope.value.code in {"source_out_of_scope", "invalid_source_config"}

        monkeypatch.setenv("WLH_ALLOW_LOOPBACK", "0")
        loopback_yaml = tmp_path / "loopback.yaml"
        loopback_yaml.write_text(
            """
id: dd-999
scheduler:
  scope: "Loopback test"
  entry_urls:
    - "http://127.0.0.1:8000/laws/"
  allowed_hosts:
    - "127.0.0.1"
  allowed_path_prefixes:
    - "/laws/"
fetcher:
  mode: http
processor:
  parser:
    type: html
cleaners: {}
storage:
  archive_approved: true
""".strip(),
            encoding="utf-8",
        )
        with pytest.raises(WlhError) as exc_loopback:
            load_file(loopback_yaml)
        assert exc_loopback.value.code in {"non_public_address", "invalid_source_config"}

    def test_all_registered_source_configs_match_schema_and_matrix_ledger(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every `sources/dd-*.yaml` passes strict public-network schema validation and aligns with the matrix CSV and MD."""
        import csv
        import re

        from wlh.config import load_file
        from wlh.matrix import render_markdown_from_csv

        monkeypatch.setenv("WLH_ALLOW_LOOPBACK", "0")
        repo_root = Path(__file__).resolve().parents[1]
        sources_dir = repo_root / "sources"
        if not any(sources_dir.glob("dd-*.yaml")):
            pytest.skip("Private source configs not present")
        matrix_csv = repo_root / "docs" / "runbooks" / "source-adaptation-matrix.csv"
        matrix_md = repo_root / "docs" / "runbooks" / "source-adaptation-matrix.md"
        if (
            not matrix_csv.is_file()
            or not matrix_md.is_file()
            or not any(sources_dir.glob("dd-*.yaml"))
        ):
            pytest.skip("Private source configs or adaptation matrix ledger not present")

        assert render_markdown_from_csv(matrix_csv, matrix_md) == matrix_md.read_text(
            encoding="utf-8"
        ), "source-adaptation-matrix.md is out of sync with source-adaptation-matrix.csv"

        with matrix_csv.open(encoding="utf-8-sig", newline="") as handle:
            matrix_rows = {row["Source ID"]: row for row in csv.DictReader(handle)}

        yaml_files = sorted(sources_dir.glob("*.yaml"))
        assert len(yaml_files) >= 9

        configured_ids: set[str] = set()
        for yaml_path in yaml_files:
            assert re.fullmatch(r"dd-\d{3}\.yaml", yaml_path.name), (
                f"Invalid source config filename in sources/: {yaml_path.name}"
            )
            raw_text = yaml_path.read_text(encoding="utf-8")
            expected_anchor = (
                f"# 来源台账：docs/runbooks/source-adaptation-matrix.md#{yaml_path.stem}"
            )
            assert expected_anchor in raw_text, f"{yaml_path.name} missing ledger anchor comment"

            cfg = load_file(yaml_path)
            assert cfg.id == yaml_path.stem
            assert cfg.storage.archive_approved is True
            assert cfg.id in matrix_rows, f"{cfg.id} missing from source-adaptation-matrix.csv"

            row = matrix_rows[cfg.id]
            csv_adapters = {
                part.strip()
                for part in row["推荐适配器"].split("（")[0].split("(")[0].split("/")
            }
            assert cfg.processor.parser.type in csv_adapters, (
                f"{cfg.id} parser mismatch between YAML ({cfg.processor.parser.type}) and CSV ({row['推荐适配器']})"
            )
            assert row["资料类型"] == cfg.storage.metadata.document_type, (
                f"{cfg.id} document_type mismatch between YAML ({cfg.storage.metadata.document_type}) and CSV ({row['资料类型']})"
            )
            configured_ids.add(cfg.id)

        verified_in_csv = {
            source_id
            for source_id, row in matrix_rows.items()
            if row["状态"].startswith(("[x]", "[~]"))
        }
        assert verified_in_csv.issubset(configured_ids), (
            f"Matrix [x]/[~] sources missing YAML files: {verified_in_csv - configured_ids}"
        )

    def test_matrix_partial_status_renders_tilde_checkbox_and_bolds_note_prefix(
        self, tmp_path: Path
    ) -> None:
        """Matrix CSV rows with `[~] 部分跑通` render `| [~] |` and bold the `部分跑通...：` note prefix."""
        from wlh.matrix import render_markdown_from_csv

        csv_path = tmp_path / "source-adaptation-matrix.csv"
        md_path = tmp_path / "source-adaptation-matrix.md"
        csv_path.write_text(
            "Source ID,大洲/区域,法域/国家,业务子分类,资料类型,来源名称,官方入口 URL,优先级,推荐适配器,状态,备注与实测结论\n"
            "dd-492,1. 亚洲 (Asia),1.3 越南 (Vietnam),—,法律法规,Cổng Thông tin điện tử Chính phủ,https://vanban.chinhphu.vn/,P0,web (sources/dd-492.yaml),[x] 已实网跑通,实网已跑通（2026-10-25）：采集与离线重解析通过。\n"
            "dd-495,1. 亚洲 (Asia),1.3 越南 (Vietnam),—,法律法规,LuatVietnam 法规库,https://luatvietnam.vn/,P0,web (sources/dd-495.yaml),[~] 部分跑通,部分跑通（公开样本 5/10 篇）：部分文件需登录会员下载。\n"
            "dd-498,1. 亚洲 (Asia),1.3 越南 (Vietnam),—,法律法规,Thư Viện Pháp Luật,https://thuvienphapluat.vn/,P1,web,[ ] 实测受阻,实测受阻（Cloudflare 拦截）：待接入专用通道。\n",
            encoding="utf-8",
        )
        rendered = render_markdown_from_csv(csv_path, md_path)
        partial_lines = [line for line in rendered.splitlines() if 'id="dd-495"' in line]
        assert len(partial_lines) == 1
        partial_line = partial_lines[0]
        assert "| [~] |" in partial_line
        assert "**部分跑通（公开样本 5/10 篇）**：部分文件需登录会员下载。" in partial_line

    def test_matrix_multi_entry_urls_render_distinct_markdown_links(
        self, tmp_path: Path
    ) -> None:
        """Matrix CSV rows with multiple entry URLs render distinct Markdown links separated by <br>."""
        from wlh.matrix import render_markdown_from_csv

        csv_path = tmp_path / "source-adaptation-matrix.csv"
        md_path = tmp_path / "source-adaptation-matrix.md"
        csv_path.write_text(
            "Source ID,大洲/区域,法域/国家,业务子分类,资料类型,来源名称,官方入口 URL,优先级,推荐适配器,状态,备注与实测结论\n"
            'dd-509,1. 亚洲法域 (Asia),1.3 中国台湾 (Taiwan),—,法律法规,全国法规资料库（LawBank Taiwan）,"https://law.moj.gov.tw/ENG/Law/LawSearchLaw.aspx, https://law.moj.gov.tw/ENG/Law/LawSearchAgree.aspx",P0,web / structured_xml,[ ] 实测受阻,实测受阻说明\n',
            encoding="utf-8",
        )
        rendered = render_markdown_from_csv(csv_path, md_path)
        multi_lines = [line for line in rendered.splitlines() if 'id="dd-509"' in line]
        assert len(multi_lines) == 1
        line = multi_lines[0]
        assert "[全国法规资料库（LawBank Taiwan）（法规检索）](https://law.moj.gov.tw/ENG/Law/LawSearchLaw.aspx)" in line
        assert "[全国法规资料库（LawBank Taiwan）（条约协定）](https://law.moj.gov.tw/ENG/Law/LawSearchAgree.aspx)" in line
        assert "<br>" in line

    def test_collection_sources_prohibit_single_document_reward_hacking_shortcuts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All multi-document collection sources in `sources/dd-*.yaml` (except single-standard `dd-375`) enforce multi-document discovery invariants."""
        import re
        from urllib.parse import urlsplit

        from wlh.config import load_file

        monkeypatch.setenv("WLH_ALLOW_LOOPBACK", "0")
        repo_root = Path(__file__).resolve().parents[1]
        sources_dir = repo_root / "sources"
        if not any(sources_dir.glob("dd-*.yaml")):
            pytest.skip("Private source configs not present")
        forbidden_prefix_exts = (
            ".html",
            ".htm",
            ".shtml",
            ".jsp",
            ".aspx",
            ".pdf",
            ".doc",
            ".docx",
            ".zip",
            ".xml",
        )
        forbidden_entry_exts = (
            ".pdf",
            ".doc",
            ".docx",
            ".rtf",
            ".xls",
            ".xlsx",
            ".zip",
        )
        single_item_markers = (
            "ItemID=",
            "docid=",
            "dDocName=",
            "/lawdata/",
            "/data.xml",
            "/item/",
        )

        for yaml_path in sorted(sources_dir.glob("dd-*.yaml")):
            cfg = load_file(yaml_path)
            if cfg.id == "dd-375":
                continue

            # a) max_depth >= 1
            assert cfg.scheduler.max_depth >= 1, (
                f"{cfg.id} must have scheduler.max_depth >= 1 (single-page max_depth=0 forbidden)"
            )

            # b) budget.documents >= 10
            assert cfg.scheduler.budget.documents >= 10, (
                f"{cfg.id} must have scheduler.budget.documents >= 10 (lowering default budget to 1 is forbidden)"
            )

            # c) attachment_url_pattern must match multiple distinct attachment filenames under allowed scope
            docs = cfg.processor.parser.documents
            att_pat = docs.attachment_url_pattern or ""
            att_re = re.compile(att_pat, re.I)
            candidate_triplets: list[tuple[str, str, str]] = []
            for host in cfg.scheduler.allowed_hosts:
                prefixes = (
                    cfg.scheduler.host_path_scopes.get(host)
                    or cfg.scheduler.allowed_path_prefixes
                )
                for prefix in prefixes:
                    base = f"https://{host}{prefix.rstrip('/')}"
                    candidate_triplets.append(
                        (
                            f"{base}/doc_alpha_01.pdf",
                            f"{base}/doc_beta_02.pdf",
                            f"{base}/official_annex_03.pdf",
                        )
                    )
                    candidate_triplets.append(
                        (
                            f"{base}/item/100/doc_alpha_01.pdf",
                            f"{base}/item/100/doc_beta_02.pdf",
                            f"{base}/item/100/official_annex_03.pdf",
                        )
                    )
                    candidate_triplets.append(
                        (
                            f"{base}/doc_alpha_01.docx",
                            f"{base}/doc_beta_02.docx",
                            f"{base}/official_annex_03.docx",
                        )
                    )
                    candidate_triplets.append(
                        (
                            f"{base}/item/100/doc_alpha_01.docx",
                            f"{base}/item/100/doc_beta_02.docx",
                            f"{base}/item/100/official_annex_03.docx",
                        )
                    )
            assert any(
                bool(att_re.search(u1)) and bool(att_re.search(u2)) and bool(att_re.search(u3))
                for u1, u2, u3 in candidate_triplets
            ), (
                f"{cfg.id} attachment_url_pattern {att_pat!r} must match multiple distinct filenames "
                "(disabling attachments or locking onto a single filename is forbidden)"
            )

            # d) entry_urls and detail_url_patterns invariants
            parser_type = cfg.processor.parser.type
            for entry_url in cfg.scheduler.entry_urls:
                entry_path = urlsplit(entry_url).path.lower()
                assert not entry_path.endswith(forbidden_entry_exts), (
                    f"{cfg.id} entry_url {entry_url!r} must not point directly to a binary/document file"
                )
                assert not any(marker in entry_url for marker in single_item_markers), (
                    f"{cfg.id} entry_url {entry_url!r} contains a single-item marker"
                )
                if parser_type == "html":
                    assert not att_re.search(entry_url), (
                        f"{cfg.id} entry_url {entry_url!r} must not match attachment_url_pattern"
                    )

            if parser_type == "html":
                assert (
                    bool(docs.detail_url_patterns)
                    or docs.detail_body == "never"
                ), (
                    f"{cfg.id} ({parser_type}) must declare detail_url_patterns unless detail_body == 'never'"
                )

            if docs.detail_url_patterns:
                for entry_url in cfg.scheduler.entry_urls:
                    for pat in docs.detail_url_patterns:
                        assert not re.search(pat, entry_url), (
                            f"{cfg.id} entry_url {entry_url!r} matches detail_url_pattern {pat!r} "
                            "(Entry-is-Detail shortcut is forbidden)"
                        )

            # e) No prefix in allowed_path_prefixes or host_path_scopes may lock onto a single file extension
            all_prefixes = list(cfg.scheduler.allowed_path_prefixes)
            for host, scoped_prefixes in cfg.scheduler.host_path_scopes.items():
                if cfg.scheduler.allowed_path_prefixes != ["/"]:
                    for prefix in scoped_prefixes:
                        assert prefix in cfg.scheduler.allowed_path_prefixes, (
                            f"{cfg.id} host_path_scopes[{host!r}] prefix {prefix!r} must be present in allowed_path_prefixes"
                        )
                all_prefixes.extend(scoped_prefixes)
            for prefix in all_prefixes:
                assert not prefix.lower().endswith(forbidden_prefix_exts), (
                    f"{cfg.id} path prefix {prefix!r} locks onto a single file path"
                )

            # f) For any parser where "max_pages" in parser.options, assert max_pages >= 2
            if "max_pages" in cfg.processor.parser.options:
                assert int(cfg.processor.parser.options["max_pages"]) >= 2, (
                    f"{cfg.id} processor.parser.options.max_pages must be >= 2"
                )

            # g) content_xpath must not truncate multi-block content containers
            if docs.content_xpath:
                assert not re.search(
                    r"/(?:div|p|section|article)\[\s*(?:[12]|position\(\)\s*(?:<=?\s*[12]|<\s*[123]|!=\s*3|=\s*[12])|last\(\))\s*\]\s*$",
                    docs.content_xpath,
                ), (
                    f"{cfg.id} content_xpath {docs.content_xpath!r} must not truncate multi-block content containers"
                )

            # h) content_xpath must never reference metadata attribute tables or outer layout/tab chrome
            if docs.content_xpath:
                forbidden_xpath_tokens = (
                    '@id="thuoctinh"',
                    "@id='thuoctinh'",
                    '@id="left_col"',
                    "@id='left_col'",
                    '@id="tabContent1"',
                    "@id='tabContent1'",
                    "info-dt-l",
                )
                for bad_tok in forbidden_xpath_tokens:
                    assert bad_tok not in docs.content_xpath, (
                        f"{cfg.id} content_xpath {docs.content_xpath!r} must not reference "
                        f"metadata/layout chrome token {bad_tok!r}"
                    )

            # i) document_number regex must never match draft version labels like "Lần dự thảo 4"
            doc_num_rule = docs.fields.get("document_number")
            if doc_num_rule and doc_num_rule.regex:
                assert not bool(re.search(doc_num_rule.regex, "Lần dự thảo 4", re.I)), (
                    f"{cfg.id} document_number regex {doc_num_rule.regex!r} must not match draft version labels"
                )

            # j) For dd-493, attachment_url_pattern must not match both .docx and .pdf simultaneously
            if cfg.id == "dd-493":
                matches_pdf = bool(att_re.search("https://congbao.chinhphu.vn/files/doc_01.pdf"))
                matches_docx = bool(att_re.search("https://congbao.chinhphu.vn/files/doc_01.docx"))
                assert not (matches_pdf and matches_docx), (
                    f"{cfg.id} attachment_url_pattern {att_pat!r} must not match both .docx and .pdf simultaneously"
                )

    def test_concurrency_up_to_32_loads_and_above_32_raises_validation_error(
        self, tmp_path: Path
    ) -> None:
        """Source configs with `concurrency` up to 32 load cleanly, whereas `concurrency` of 0 or above 32 raises `invalid_source_config`."""
        from wlh.config import load_file
        from wlh.core import WlhError

        base_template = """
id: dd-499
scheduler:
  scope: "Federal Register API concurrency test"
  concurrency: {concurrency}
  entry_urls:
    - "https://www.federalregister.gov/api/v1/documents.json?per_page=2"
  allowed_hosts:
    - "www.federalregister.gov"
  allowed_path_prefixes:
    - "/api/v1/"
fetcher:
  mode: http
processor:
  parser:
    type: json
cleaners: {{}}
storage:
  archive_approved: true
  metadata:
    source_name: "Federal Register"
    document_type: "法律法规"
    language: "en"
    jurisdiction: "US"
""".strip()

        for allowed_c in (12, 32):
            yaml_path = tmp_path / f"concurrency_{allowed_c}.yaml"
            yaml_path.write_text(
                base_template.format(concurrency=allowed_c),
                encoding="utf-8",
            )
            cfg = load_file(yaml_path)
            assert cfg.scheduler.concurrency == allowed_c

        for invalid_c in (0, 33):
            yaml_path = tmp_path / f"concurrency_{invalid_c}.yaml"
            yaml_path.write_text(
                base_template.format(concurrency=invalid_c),
                encoding="utf-8",
            )
            with pytest.raises(WlhError) as exc_info:
                load_file(yaml_path)
            assert exc_info.value.code == "invalid_source_config"
