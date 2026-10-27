"""Black-box contract tests for database initialization, S3 CAS storage, and source configuration."""

from __future__ import annotations

import hashlib
from pathlib import Path

import psycopg
import pytest


class TestDatabaseInitialization:
    def test_repeated_init_creates_required_schema_tables_idempotently(
        self, initialized_db: dict[str, str]
    ) -> None:
        """Running database initialization repeatedly succeeds and ensures all `wlh` tables exist."""
        from wlh.db import initialize

        second = initialize()
        assert second["status"] in {"initialized", "already_initialized"}

        expected_tables = {
            "migration",
            "source",
            "frontier",
            "decision",
            "run",
            "acquisition",
            "task",
            "task_input",
            "resource",
            "observation",
            "observation_evidence",
            "delivery_conflict",
            "version",
            "document_row",
            "snapshot",
            "snapshot_source",
            "snapshot_item",
            "snapshot_file",
            "publication",
            "snapshot_publication",
        }
        with psycopg.connect(initialized_db["WLH_DATABASE_URL"]) as conn:
            rows = conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'wlh'"
            ).fetchall()
            found = {r[0] for r in rows}
            assert expected_tables.issubset(found), f"Missing tables: {expected_tables - found}"

    def test_pytest_runtime_rejects_non_test_database_and_non_test_s3_bucket(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """During pytest execution, connecting to a non-test database or non-test S3 bucket raises an isolation error."""
        import os
        from urllib.parse import urlsplit

        from wlh.core import WlhError
        from wlh.db import connect, dsn, initialize
        from wlh.engine.objects import Objects

        active_db = urlsplit(os.environ["WLH_DATABASE_URL"]).path.lstrip("/")
        assert active_db.endswith("_test") or active_db.startswith("test_")
        assert active_db not in {"wlh", "wlh_issue4_full"}

        active_bucket = os.environ["WLH_S3_BUCKET"]
        assert active_bucket.endswith(("-test", "_test")) or active_bucket.startswith(
            ("test-", "test_")
        )
        assert active_bucket != "wlh-archive"

        for forbidden_dsn in (
            "postgresql://wlh:wlh@127.0.0.1:15432/wlh",
            "postgresql://wlh:wlh@127.0.0.1:15432/wlh_issue4_full",
        ):
            monkeypatch.setenv("WLH_DATABASE_URL", forbidden_dsn)
            with pytest.raises(WlhError) as exc_dsn:
                dsn()
            assert exc_dsn.value.code == "test_database_isolation_violation"

            with pytest.raises(WlhError) as exc_conn:
                connect()
            assert exc_conn.value.code == "test_database_isolation_violation"

            with pytest.raises(WlhError) as exc_init:
                initialize()
            assert exc_init.value.code == "test_database_isolation_violation"

        monkeypatch.setenv("WLH_S3_BUCKET", "wlh-archive")
        with pytest.raises(WlhError) as exc_obj:
            Objects()
        assert exc_obj.value.code == "test_object_storage_isolation_violation"


class TestContentAddressableStorage:
    def test_valid_payload_returns_sha256_and_reads_back_identical_bytes(
        self, wlh_env: dict[str, str]
    ) -> None:
        """Storing bytes in CAS returns their SHA-256 digest and retrieves the exact payload."""
        from wlh.engine.objects import Objects

        objects = Objects()
        objects.check()

        payload = b"WorldLawHarvester S3 CAS immutability verification payload \xe6\xb3\x95\xe5\xbe\x8b"
        expected_sha = hashlib.sha256(payload).hexdigest()

        returned_sha = objects.put(payload)
        assert returned_sha == expected_sha

        read_back = objects.get(expected_sha)
        assert read_back == payload

    def test_missing_or_malformed_hash_raises_archive_error(
        self, wlh_env: dict[str, str]
    ) -> None:
        """Requesting an unknown or malformed SHA-256 digest raises a deterministic archive error."""
        from wlh.core import WlhError
        from wlh.engine.objects import Objects

        objects = Objects()
        objects.check()

        missing_sha = "f" * 64
        with pytest.raises(WlhError) as exc_info:
            objects.get(missing_sha)
        assert exc_info.value.code == "archive_missing"

        with pytest.raises(WlhError) as exc_info2:
            objects.get("not-a-sha256")
        assert exc_info2.value.code == "archive_invalid"


class TestSourceConfigValidation:
    def test_approved_in_scope_config_loads_successfully(self, tmp_path: Path) -> None:
        """A source configuration with archive approval and in-scope entry URLs loads cleanly."""
        from wlh.config import ADAPTERS, load_file

        required_adapters = {
            "web",
            "federal_register",
            "structured_xml",
            "worldwidelaw_bridge",
            "juriscraper_bridge",
            "oldp_bridge",
            "openstates_bridge",
            "opencaselaw_bridge",
        }
        assert required_adapters.issubset(set(ADAPTERS))

        valid_yaml = tmp_path / "valid.yaml"
        valid_yaml.write_text(
            """
id: dd-499
adapter: federal_register
archive_approved: true
scope: "Federal Register API test scope"
entry_urls:
  - "https://www.federalregister.gov/api/v1/documents.json?per_page=2"
allowed_hosts:
  - "www.federalregister.gov"
allowed_path_prefixes:
  - "/api/v1/"
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
        assert cfg.adapter == "federal_register"

    def test_unapproved_or_out_of_scope_or_loopback_config_raises_validation_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Configs missing archive approval, violating host/path scope, or targeting loopback are rejected."""
        from wlh.config import load_file
        from wlh.core import WlhError

        base_yaml = """
id: dd-499
adapter: federal_register
archive_approved: true
scope: "Federal Register API test scope"
entry_urls:
  - "https://www.federalregister.gov/api/v1/documents.json?per_page=2"
allowed_hosts:
  - "www.federalregister.gov"
allowed_path_prefixes:
  - "/api/v1/"
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
adapter: web
archive_approved: true
scope: "Loopback test"
entry_urls:
  - "http://127.0.0.1:8000/laws/"
allowed_hosts:
  - "127.0.0.1"
allowed_path_prefixes:
  - "/laws/"
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
        matrix_csv = repo_root / "docs" / "runbooks" / "source-adaptation-matrix.csv"
        matrix_md = repo_root / "docs" / "runbooks" / "source-adaptation-matrix.md"

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
            assert cfg.archive_approved is True
            assert cfg.id in matrix_rows, f"{cfg.id} missing from source-adaptation-matrix.csv"

            row = matrix_rows[cfg.id]
            csv_adapters = {
                part.strip()
                for part in row["推荐适配器"].split("（")[0].split("(")[0].split("/")
            }
            assert cfg.adapter in csv_adapters, (
                f"{cfg.id} adapter mismatch between YAML ({cfg.adapter}) and CSV ({row['推荐适配器']})"
            )
            assert row["资料类型"] == cfg.metadata.document_type, (
                f"{cfg.id} document_type mismatch between YAML ({cfg.metadata.document_type}) and CSV ({row['资料类型']})"
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
            assert cfg.fetch.max_depth >= 1, (
                f"{cfg.id} must have fetch.max_depth >= 1 (single-page max_depth=0 forbidden)"
            )

            # b) budget.documents >= 10
            assert cfg.budget.documents >= 10, (
                f"{cfg.id} must have budget.documents >= 10 (lowering default budget to 1 is forbidden)"
            )

            # c) attachment_url_pattern must match multiple distinct attachment filenames under allowed scope
            att_pat = cfg.documents.attachment_url_pattern or ""
            att_re = re.compile(att_pat, re.I)
            candidate_triplets: list[tuple[str, str, str]] = []
            for host in cfg.allowed_hosts:
                prefixes = cfg.host_path_scopes.get(host) or cfg.allowed_path_prefixes
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
            for entry_url in cfg.entry_urls:
                entry_path = urlsplit(entry_url).path.lower()
                assert not entry_path.endswith(forbidden_entry_exts), (
                    f"{cfg.id} entry_url {entry_url!r} must not point directly to a binary/document file"
                )
                assert not any(marker in entry_url for marker in single_item_markers), (
                    f"{cfg.id} entry_url {entry_url!r} contains a single-item marker"
                )
                if cfg.adapter in {"web", "worldwidelaw_bridge"}:
                    assert not att_re.search(entry_url), (
                        f"{cfg.id} entry_url {entry_url!r} must not match attachment_url_pattern"
                    )

            if cfg.adapter in {"web", "worldwidelaw_bridge"}:
                assert (
                    bool(cfg.documents.detail_url_patterns)
                    or cfg.documents.detail_body == "never"
                ), (
                    f"{cfg.id} ({cfg.adapter}) must declare detail_url_patterns unless detail_body == 'never'"
                )

            if cfg.documents.detail_url_patterns:
                for entry_url in cfg.entry_urls:
                    for pat in cfg.documents.detail_url_patterns:
                        assert not re.search(pat, entry_url), (
                            f"{cfg.id} entry_url {entry_url!r} matches detail_url_pattern {pat!r} "
                            "(Entry-is-Detail shortcut is forbidden)"
                        )

            # e) No prefix in allowed_path_prefixes or host_path_scopes may lock onto a single file extension
            all_prefixes = list(cfg.allowed_path_prefixes)
            for host, scoped_prefixes in cfg.host_path_scopes.items():
                if cfg.allowed_path_prefixes != ["/"]:
                    for prefix in scoped_prefixes:
                        assert prefix in cfg.allowed_path_prefixes, (
                            f"{cfg.id} host_path_scopes[{host!r}] prefix {prefix!r} must be present in allowed_path_prefixes"
                        )
                all_prefixes.extend(scoped_prefixes)
            for prefix in all_prefixes:
                assert not prefix.lower().endswith(forbidden_prefix_exts), (
                    f"{cfg.id} path prefix {prefix!r} locks onto a single file path"
                )

            # f) For any adapter where "max_pages" in adapter_options, assert max_pages >= 2
            if "max_pages" in cfg.adapter_options:
                assert int(cfg.adapter_options["max_pages"]) >= 2, (
                    f"{cfg.id} adapter_options.max_pages must be >= 2"
                )

            # g) content_xpath must not truncate multi-block content containers
            if cfg.documents.content_xpath:
                assert not re.search(
                    r"/(?:div|p|section|article)\[\s*(?:[12]|position\(\)\s*(?:<=?\s*[12]|<\s*[123]|!=\s*3|=\s*[12])|last\(\))\s*\]\s*$",
                    cfg.documents.content_xpath,
                ), (
                    f"{cfg.id} content_xpath {cfg.documents.content_xpath!r} must not truncate multi-block content containers"
                )

            # h) content_xpath must never reference metadata attribute tables or outer layout/tab chrome
            if cfg.documents.content_xpath:
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
                    assert bad_tok not in cfg.documents.content_xpath, (
                        f"{cfg.id} content_xpath {cfg.documents.content_xpath!r} must not reference "
                        f"metadata/layout chrome token {bad_tok!r}"
                    )

            # i) document_number regex must never match draft version labels like "Lần dự thảo 4"
            doc_num_rule = cfg.documents.fields.get("document_number")
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
adapter: federal_register
archive_approved: true
scope: "Federal Register API concurrency test"
concurrency: {concurrency}
entry_urls:
  - "https://www.federalregister.gov/api/v1/documents.json?per_page=2"
allowed_hosts:
  - "www.federalregister.gov"
allowed_path_prefixes:
  - "/api/v1/"
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
            assert cfg.concurrency == allowed_c

        for invalid_c in (0, 33):
            yaml_path = tmp_path / f"concurrency_{invalid_c}.yaml"
            yaml_path.write_text(
                base_template.format(concurrency=invalid_c),
                encoding="utf-8",
            )
            with pytest.raises(WlhError) as exc_info:
                load_file(yaml_path)
            assert exc_info.value.code == "invalid_source_config"



class TestRunbookVerificationSamples:
    def test_sample_renderer_builds_two_column_payload_and_markdown_table_capped_at_ten_items(
        self,
    ) -> None:
        """Rendering verification samples produces a standalone HTML two-column Payload/Markdown comparison document with attachment links and a 10-item cap."""
        import re

        from wlh.runbook_samples import (
            render_source_verification_html,
        )

        items: list[dict[str, object]] = [
            {
                "detail_url": "https://vanban.chinhphu.vn/page/pagenew.aspx?docid=210001",
                "attachment_url": "https://vanban.chinhphu.vn/cpp/files/vbpq/2026/04/105-nd.signed.pdf",
                "original_file": "Nghị định 105_2026_NĐ-CP.pdf",
                "original_media_type": "application/pdf",
                "raw_payload": "<html><body><div>Chi tiết văn bản 105/2026/NĐ-CP</div></body></html>",
                "markdown": "# Nghị định 105/2026/NĐ-CP\n\nĐiều 1. Phạm vi điều chỉnh.",
                "title": "Nghị định 105/2026/NĐ-CP",
                "doc_number": "105/2026/NĐ-CP",
                "issuer": "Chính phủ",
                "published_at": "2026-04-15",
            },
            {
                "detail_url": "https://example.gov.vn/detail/2",
                "attachment_url": "https://example.gov.vn/files/doc2.docx",
                "original_file": "doc2.docx",
                "original_media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "raw_payload": "",
                "markdown": "# Quyết định 02/2026\n\nNội dung toàn văn.",
                "title": "Quyết định 02/2026",
                "doc_number": "02/2026/QĐ-TTg",
                "issuer": "Thủ tướng Chính phủ",
                "published_at": "2026-04-16",
            },
        ]
        for i in range(3, 13):
            items.append(
                {
                    "detail_url": f"https://example.gov.vn/detail/{i}",
                    "attachment_url": None,
                    "original_file": None,
                    "original_media_type": "text/html",
                    "raw_payload": f"<div class='law'>Raw HTML payload #{i}</div>",
                    "markdown": f"# Law #{i}\n\nArticle 1 of Law #{i}.",
                    "title": f"Law #{i}",
                    "doc_number": f"{i:02d}/2026/QH15",
                    "issuer": "Quốc hội",
                    "published_at": f"2026-04-{i:02d}",
                }
            )

        html_doc = render_source_verification_html(
            [
                {
                    "source_id": "dd-492",
                    "source_name": "越南政府文献门户",
                    "status": "[x] 已实网跑通",
                    "continent": "1. 亚洲法域 (Asia)",
                    "jurisdiction": "1.4 越南 (Vietnam)",
                    "subcategory": "—",
                    "items": items,
                }
            ],
            max_items_per_source=10,
        )

        assert "<!doctype html>" in html_doc.lower()
        assert "<html" in html_doc.lower()
        assert "<style>" in html_doc.lower()
        assert "<table" in html_doc.lower()
        assert "<tr" in html_doc.lower()
        assert "<td" in html_doc.lower()
        assert re.search(r'<aside\b[^>]*class="[^"]*sidebar[^"]*"', html_doc), (
            "Expected GitBook-style left <aside class='...sidebar...'> container"
        )
        assert re.search(r'<main\b[^>]*class="[^"]*main[^"]*"', html_doc), (
            "Expected GitBook-style right <main class='...main...'> container"
        )
        assert "1. 亚洲法域 (Asia)" in html_doc
        assert "1.4 越南 (Vietnam)" in html_doc
        assert 'href="#dd-492"' in html_doc
        assert 'id="dd-492"' in html_doc
        assert ("左栏" in html_doc or "请求 Payload" in html_doc) and (
            "右栏" in html_doc or "Markdown" in html_doc
        )
        assert re.search(
            r'<a\b[^>]*href="https://vanban\.chinhphu\.vn/cpp/files/vbpq/2026/04/105-nd\.signed\.pdf"',
            html_doc,
        ), "Expected clickable HTML <a> link for PDF attachment"
        assert re.search(
            r'<a\b[^>]*href="https://example\.gov\.vn/files/doc2\.docx"',
            html_doc,
        ), "Expected clickable HTML <a> link for DOCX attachment"
        assert "Raw HTML payload #3" in html_doc
        assert "Article 1 of Law #3" in html_doc
        assert "Law #10" in html_doc
        assert "Law #11" not in html_doc
        assert "Law #12" not in html_doc

    def test_csv_to_html_one_way_sync_updates_gitbook_toc_and_preserves_samples(
        self, tmp_path: Path
    ) -> None:
        """Syncing `source-adaptation-matrix.csv` -> `source-verification-samples.html` updates the GitBook TOC and metadata while preserving sample rows."""
        import importlib
        from typing import Any

        from wlh.runbook_samples import (
            render_html_from_csv,
            render_source_verification_html,
            sync_csv_to_html,
        )

        lxml_html: Any = importlib.import_module("lxml.html")

        csv_path = tmp_path / "source-adaptation-matrix.csv"
        html_path = tmp_path / "source-verification-samples.html"

        initial_html = render_source_verification_html(
            [
                {
                    "source_id": "dd-492",
                    "source_name": "Cổng Thông tin điện tử Chính phủ",
                    "status": "[x] 已实网跑通",
                    "continent": "1. 亚洲法域 (Asia)",
                    "jurisdiction": "1.4 越南 (Vietnam)",
                    "subcategory": "—",
                    "items": [
                        {
                            "detail_url": "https://vanban.chinhphu.vn/page/pagenew.aspx?docid=210001",
                            "attachment_url": "https://vanban.chinhphu.vn/cpp/files/vbpq/2026/04/105-nd.signed.pdf",
                            "original_file": "Nghị định 105_2026_NĐ-CP.pdf",
                            "original_media_type": "application/pdf",
                            "attachments": [
                                {
                                    "url": "https://vanban.chinhphu.vn/cpp/files/vbpq/2026/04/105-nd.signed.pdf",
                                    "name": "Nghị định 105_2026_NĐ-CP.pdf",
                                    "media_type": "application/pdf",
                                },
                                {
                                    "url": "https://vanban.chinhphu.vn/cpp/files/vbpq/2026/04/105-nd-phu-luc.docx",
                                    "name": "Phụ lục Nghị định 105.docx",
                                    "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                },
                            ],
                            "raw_payload": "<html><body><div>Chi tiết văn bản 105/2026/NĐ-CP</div></body></html>",
                            "markdown": "# Nghị định 105/2026/NĐ-CP\n\nĐiều 1. Phạm vi điều chỉnh.",
                            "title": "Nghị định 105/2026/NĐ-CP",
                            "doc_number": "105/2026/NĐ-CP",
                            "issuer": "Chính phủ",
                            "published_at": "2026-04-15",
                        }
                    ],
                },
                {
                    "source_id": "dd-495",
                    "source_name": "LuatVietnam 法规库（旧名称）",
                    "status": "[~] 部分跑通",
                    "continent": "1. 亚洲法域 (Asia)",
                    "jurisdiction": "1.4 越南 (Vietnam)",
                    "subcategory": "—",
                    "items": [
                        {
                            "detail_url": "https://thuvienphapluat.vn/van-ban/Thue-Phi-Le-Phi/Thong-tu-12-2026.aspx",
                            "attachment_url": None,
                            "original_file": None,
                            "original_media_type": "text/html",
                            "raw_payload": "<div id='toanvan'>Toàn văn Thông tư 12/2026/TT-BTC hướng dẫn chế độ kế toán.</div>",
                            "markdown": "# Thông tư 12/2026/TT-BTC\n\nĐiều 1. Quy định chung về kế toán.",
                            "title": "Thông tư 12/2026/TT-BTC",
                            "doc_number": "12/2026/TT-BTC",
                            "issuer": "Bộ Tài chính",
                            "published_at": "2026-03-20",
                        }
                    ],
                },
            ],
            max_items_per_source=10,
        )
        html_path.write_text(initial_html, encoding="utf-8")

        csv_path.write_text(
            "Source ID,大洲/区域,法域/国家,业务子分类,资料类型,来源名称,官方入口 URL,优先级,推荐适配器,状态,备注与实测结论\n"
            "dd-492,1. 亚洲法域 (Asia),1.4 越南 (Vietnam),1.4.1 越南核心法规,法律法规,Cổng Thông tin điện tử Chính phủ,https://vanban.chinhphu.vn/,P0,web (sources/dd-492.yaml),[x] 已实网跑通,实网已跑通（2026-10-25）：采集与离线重解析通过。\n"
            "dd-495,1. 亚洲法域 (Asia),1.4 越南 (Vietnam),1.4.1 越南核心法规,法律法规,LuatVietnam 法规库（已更新名称）,https://thuvienphapluat.vn/,P0,web (sources/dd-495.yaml),[~] 部分跑通,部分跑通（公开样本 5/10 篇）：已同步最新结论。\n"
            "dd-498,1. 亚洲法域 (Asia),1.4 越南 (Vietnam),1.4.1 越南核心法规,法律法规,Thư Viện Pháp Luật 待适配项,https://thuvienphapluat.vn/,P1,web,[ ] 待适配,待适配说明。\n",
            encoding="utf-8",
        )

        updated = sync_csv_to_html(csv_path, html_path)
        assert updated is True
        second = sync_csv_to_html(csv_path, html_path)
        assert second is False

        synced_text = html_path.read_text(encoding="utf-8")
        assert render_html_from_csv(csv_path, html_path) == synced_text
        assert "dd-498" not in synced_text
        assert "LuatVietnam 法规库（已更新名称）" in synced_text
        assert "1.4.1 越南核心法规" in synced_text
        assert "https://vanban.chinhphu.vn/cpp/files/vbpq/2026/04/105-nd.signed.pdf" in synced_text
        assert "https://vanban.chinhphu.vn/cpp/files/vbpq/2026/04/105-nd-phu-luc.docx" in synced_text
        assert "Phụ lục Nghị định 105.docx" in synced_text
        assert "Điều 1. Phạm vi điều chỉnh." in synced_text
        assert "Toàn văn Thông tư 12/2026/TT-BTC" in synced_text
        assert "Điều 1. Quy định chung về kế toán." in synced_text

        tree: Any = lxml_html.fromstring(synced_text)
        sidebar_text = str(tree.xpath('//aside[contains(@class, "sidebar")]')[0].text_content())
        main_text = str(tree.xpath('//main[contains(@class, "main")]')[0].text_content())
        assert "LuatVietnam 法规库（已更新名称）" in sidebar_text
        assert "1.4.1 越南核心法规" in sidebar_text
        assert "LuatVietnam 法规库（已更新名称）" in main_text

    def test_published_runbook_samples_cover_all_active_and_partial_sources_without_reward_hacked_stubs(
        self,
    ) -> None:
        """`docs/runbooks/source-verification-samples.html` covers all `[x]`/`[~]` sources with GitBook TOC synced from CSV and <=10 clean two-column HTML samples."""
        import csv
        import importlib
        import re
        from typing import Any

        import wlh.runbook_samples as runbook_samples
        from wlh.runbook_samples import (
            render_html_from_csv,
        )

        assert not hasattr(runbook_samples, "_restore_origin_url"), (
            "Rewriting 127.0.0.1 loopback fixture URLs via _restore_origin_url is forbidden"
        )

        lxml_html: Any = importlib.import_module("lxml.html")

        repo_root = Path(__file__).resolve().parents[1]
        matrix_csv = repo_root / "docs" / "runbooks" / "source-adaptation-matrix.csv"
        matrix_md = repo_root / "docs" / "runbooks" / "source-adaptation-matrix.md"
        samples_html = repo_root / "docs" / "runbooks" / "source-verification-samples.html"
        samples_md = repo_root / "docs" / "runbooks" / "source-verification-samples.md"

        with matrix_csv.open(encoding="utf-8-sig", newline="") as handle:
            active_ids = [
                row["Source ID"]
                for row in csv.DictReader(handle)
                if row["状态"].startswith(("[x]", "[~]"))
            ]
        assert len(active_ids) >= 40
        assert samples_html.is_file(), (
            "docs/runbooks/source-verification-samples.html must exist on disk"
        )
        assert not samples_md.exists(), (
            "Obsolete docs/runbooks/source-verification-samples.md must not exist on disk"
        )

        content = samples_html.read_text(encoding="utf-8")
        assert render_html_from_csv(matrix_csv, samples_html) == content, (
            "source-verification-samples.html is out of sync with source-adaptation-matrix.csv"
        )

        tree: Any = lxml_html.fromstring(content)

        sidebar_nodes: list[Any] = tree.xpath(
            '//aside[contains(@class, "sidebar")] | //nav[contains(@class, "toc")]'
        )
        assert len(sidebar_nodes) >= 1, (
            "Missing GitBook left sidebar TOC in source-verification-samples.html"
        )
        sidebar_el = sidebar_nodes[0]
        sidebar_text = str(sidebar_el.text_content() or "")
        for expected_heading in (
            "1. 亚洲法域 (Asia)",
            "2. 欧洲法域 (Europe)",
            "3. 美洲法域 (Americas)",
            "4. 大洋洲法域 (Oceania)",
            "5. 非洲法域与国际组织 (Africa & International Organizations)",
            "1.1 中国大陆 (China Mainland)",
            "1.4 越南 (Vietnam)",
            "1.1.1 核心官方法律法规、公报与财税库 (dd-500–dd-504)",
        ):
            assert expected_heading in sidebar_text, (
                f"Expected hierarchy heading {expected_heading!r} in GitBook left sidebar TOC"
            )

        md_text = matrix_md.read_text(encoding="utf-8")
        md_active_order = [
            m.group(1)
            for line in md_text.splitlines()
            if ("| [x] |" in line or "| [~] |" in line)
            for m in [re.search(r'<a id="(dd-\d{3})"></a>', line)]
            if m is not None
        ]
        toc_ids = [
            str(href).lstrip("#")
            for href in sidebar_el.xpath('.//a[starts-with(@href, "#dd-")]/@href')
        ]
        assert toc_ids == md_active_order, (
            "Left sidebar TOC source order must match source-adaptation-matrix.md active row order"
        )

        main_nodes: list[Any] = tree.xpath('//main[contains(@class, "main")]')
        assert len(main_nodes) >= 1, (
            "Missing GitBook right <main> container in source-verification-samples.html"
        )
        main_ids = [
            str(sec_id)
            for sec_id in main_nodes[0].xpath('.//section[starts-with(@id, "dd-")]/@id')
        ]
        assert main_ids == md_active_order, (
            "Right main pane section order must match source-adaptation-matrix.md active row order"
        )
        idx_516 = toc_ids.index("dd-516")
        assert toc_ids[idx_516 + 1] == "dd-575", (
            "dd-575 must appear immediately after dd-516 under 1.4 越南 (Vietnam)"
        )
        assert toc_ids[idx_516 + 2] == "dd-517"

        source_rows_map: dict[str, list[Any]] = {}
        for source_id in active_ids:
            nodes: list[Any] = tree.xpath(f'//*[@id="{source_id}"]')
            assert len(nodes) >= 1, (
                f'Missing element with id="{source_id}" in source-verification-samples.html'
            )
            anchor_el = nodes[0]
            sample_rows: list[Any] = anchor_el.xpath(".//tr[count(td)=2]")
            if not sample_rows:
                sample_rows = anchor_el.xpath("(./following::table[1])//tr[count(td)=2]")
            source_rows_map[source_id] = sample_rows
            row_count = len(sample_rows)
            if source_id == "dd-375":
                assert row_count == 1, f"Expected 1 sample row for dd-375, got {row_count}"
            else:
                assert row_count == 10, (
                    f"Expected exactly 10 sample rows for {source_id}, got {row_count}"
                )
            for row_idx, row in enumerate(sample_rows, start=1):
                tds: list[Any] = row.xpath("./td")
                assert len(tds) == 2, f"{source_id} row #{row_idx} must have exactly 2 <td> cells"
                left_td, right_td = tds[0], tds[1]
                left_codes: list[Any] = left_td.xpath(".//pre/code")
                for code_el in left_codes:
                    code_text = str(code_el.text_content() or "").strip()
                    assert len(code_text) >= 20, (
                        f"{source_id} sample row #{row_idx} has empty/stub left-column "
                        f"<pre><code> payload ({len(code_text)} chars)"
                    )
                att_links: list[Any] = left_td.xpath(
                    './/a[contains(@href, ".pdf") or contains(@href, ".docx") or contains(@href, ".doc") or contains(@href, "/UCMServer/TAND") or contains(@class, "attachment")]'
                )
                if not att_links:
                    assert len(left_codes) >= 1, (
                        f"{source_id} sample row #{row_idx} left <td> has no attachment link and no <pre><code> payload"
                    )
                right_codes: list[Any] = right_td.xpath(
                    ".//pre/code | .//pre | .//*[contains(@class, 'markdown')]"
                )
                right_text = (
                    str(right_codes[0].text_content() or "").strip()
                    if right_codes
                    else str(right_td.text_content() or "").strip()
                )
                assert len(right_text) >= 20, (
                    f"{source_id} sample row #{row_idx} right <td> Markdown content too short ({len(right_text)} chars)"
                )

        assert "<code></code>" not in content, (
            "Empty <code></code> raw payload blocks must not appear in source-verification-samples.html"
        )
        assert (
            "official legal document full text body content for verification."
            not in content.lower()
        ), "Unit-test fixture stub text must not appear in source-verification-samples.html"
        assert "127.0.0.1" not in content, (
            "Loopback 127.0.0.1 URLs must not appear in source-verification-samples.html"
        )
        assert "localhost" not in content, (
            "Loopback localhost URLs must not appear in source-verification-samples.html"
        )

        att_href_re = re.compile(
            r"^https://[^)\s]+(?:\.(?:pdf|docx)(?:\?[^)\s]*)?|/webcenter/ShowProperty\?nodeId=/UCMServer/TAND\d+)$",
            re.I,
        )
        for vn_att_id in ("dd-492", "dd-493", "dd-512", "dd-514", "dd-575"):
            rows = source_rows_map.get(vn_att_id, [])
            hrefs: list[str] = [
                str(href)
                for row in rows
                for href in row.xpath(".//a/@href")
            ]
            assert any(att_href_re.search(h) for h in hrefs), (
                f"{vn_att_id} section must contain clickable .pdf, .docx, or UCM download <a href='https://...'> links"
            )

        forbidden_placeholders = (
            "Bạn chưa Đăng nhập thành viên",
            "Văn bản này đang cập nhật nội dung",
            "Nội dung tóm tắt đang được cập nhật",
            "Đây là tiện ích dành cho tài khoản Tiêu chuẩn hoặc Nâng cao",
            "Vui lòng xem nội dung chi tiết trong file đính kèm",
            "Danh sách bình luận",
            "uploadFiles/host/local/du-thao-01.pdf",
            "xaydungchinhsach.chinhphu.vn/cpp/files/vbpq/2026/04/105-nd.signed.pdf",
            "Thông tư 145/2026/TT-BTC quy định đăng ký, lưu ký, bù trừ và thanh toán chứng khoán",
            "Bản án số 1463/2026/DS-PT ngày 15/10/2026 của TAND TP. Cần Thơ",
            "hoidapcstc/home/cthoidap/166356",
            "各省、自治区、直辖市人民政府，国务院各部委、各直属机构：现将有关事项通知如下。",
            "指导性案例221号：丙公司诉丁公司算法商业秘密纠纷案",
        )
        for bad_str in forbidden_placeholders:
            assert bad_str not in content, (
                f"Reward-hacked placeholder {bad_str!r} must not appear in source-verification-samples.html"
            )

        def _right_cell_markdown(row_el: Any) -> str:
            tds_local: list[Any] = row_el.xpath("./td")
            right_td_local = tds_local[1]
            codes_local: list[Any] = right_td_local.xpath(
                ".//pre/code | .//pre | .//*[contains(@class, 'markdown')]"
            )
            return (
                str(codes_local[0].text_content() or "").strip()
                if codes_local
                else str(right_td_local.text_content() or "").strip()
            )

        for src_id in ("dd-040", "dd-041"):
            for r_idx, r_el in enumerate(source_rows_map.get(src_id, []), start=1):
                md_cell = _right_cell_markdown(r_el)
                assert "【字体：大 中 小】" not in md_cell, (
                    f"{src_id} row #{r_idx} right-column Markdown contains font widget noise"
                )
                assert "### 相关新闻" not in md_cell, (
                    f"{src_id} row #{r_idx} right-column Markdown contains hidden related news heading"
                )

        for r_idx, r_el in enumerate(source_rows_map.get("dd-102", []), start=1):
            md_cell = _right_cell_markdown(r_el)
            assert "【打印】" not in md_cell and "【返回】" not in md_cell, (
                f"dd-102 row #{r_idx} right-column Markdown contains print/back button noise"
            )

        dd375_rows = source_rows_map.get("dd-375", [])
        assert len(dd375_rows) == 1
        dd375_left_td = dd375_rows[0].xpath("./td")[0]
        dd375_hrefs = " ".join(str(h) for h in dd375_left_td.xpath(".//a/@href"))
        assert "P020200612583036030033.DOCX" in dd375_hrefs, (
            "dd-375 sample row #1 left-column <td> must include attachment link P020200612583036030033.DOCX"
        )
        assert "P020210608556900301740.docx" in dd375_hrefs, (
            "dd-375 sample row #1 left-column <td> must include attachment link P020210608556900301740.docx"
        )

        for r_idx, r_el in enumerate(source_rows_map.get("dd-527", []), start=1):
            md_cell = _right_cell_markdown(r_el)
            assert "Share:" not in md_cell, (
                f"dd-527 row #{r_idx} right-column Markdown contains Share bar noise"
            )
            assert "- [](https://www.iras.gov.sg/" not in md_cell, (
                f"dd-527 row #{r_idx} right-column Markdown contains empty social share link"
            )

        for r_idx, r_el in enumerate(source_rows_map.get("dd-563", []), start=1):
            md_cell = _right_cell_markdown(r_el)
            for toc_noise in (
                "- [HIGHLIGHTS OF THIS ISSUE](",
                "- [INCOME TAX](",
                "[](https://www.irs.gov/irb/",
            ):
                assert toc_noise not in md_cell, (
                    f"dd-563 row #{r_idx} right-column Markdown contains TOC/anchor noise {toc_noise!r}"
                )

        global_forbidden_md_tokens = (
            "[](https://",
            "[](http://",
            "【字体：大 中 小】",
            "字体 [大 中 小]",
            "[【打印】]",
            "[【返回】]",
            "Đang theo dõi",
        )
        for src_id, s_rows in source_rows_map.items():
            for r_idx, r_el in enumerate(s_rows, start=1):
                md_cell = _right_cell_markdown(r_el)
                for bad_tok in global_forbidden_md_tokens:
                    assert bad_tok not in md_cell, (
                        f"{src_id} row #{r_idx} right-column Markdown contains forbidden UI noise {bad_tok!r}"
                    )

    def test_payload_preserves_multiline_structure_and_aligns_truncation_with_markdown(
        self,
    ) -> None:
        """Payload preview preserves multi-line structure and aligns truncation with right-column Markdown both in unit rendering and in the published HTML runbook."""
        import importlib
        import re
        from typing import Any

        from wlh.runbook_samples import render_source_verification_html

        lxml_html: Any = importlib.import_module("lxml.html")

        paragraphs_a = [
            f"第{i}条 纳税人应当依照税收法律、行政法规的规定履行纳税义务，按期如实办理纳税申报并缴纳税款（段落编号 A-{i:02d}）。"
            for i in range(1, 12)
        ]
        markdown_a = "\n\n".join(paragraphs_a)
        assert 550 <= len(markdown_a) <= 700
        raw_payload_a = (
            '<div class="law-body" data-jurisdiction="cn">\n'
            + "\n".join(
                f'  <p class="legal-paragraph" style="margin-bottom: 1.5em; text-indent: 2em; line-height: 1.8;">{p}</p>'
                for p in paragraphs_a
            )
            + "\n</div>"
        )
        assert 1600 <= len(raw_payload_a) <= 2000

        paragraphs_b = [
            f"第{i}条 税务机关应当加强对纳税人发票使用情况的监督检查，依法查处虚开发票等涉税违法行为（段落编号 B-{i:02d}）。"
            for i in range(1, 45)
        ]
        markdown_b = "\n\n".join(paragraphs_b)
        assert 2300 <= len(markdown_b) <= 2700
        raw_payload_b = (
            '<div class="law-body" data-jurisdiction="cn">\n'
            + "\n".join(
                f'  <p style="margin-bottom: 1.5em; text-indent: 2em;">{p}</p>'
                for p in paragraphs_b
            )
            + "\n</div>"
        )
        assert 4500 <= len(raw_payload_b) <= 5500

        rendered_unit = render_source_verification_html(
            [
                {
                    "source_id": "dd-501",
                    "source_name": "国家税务总局法规库",
                    "status": "[x] 已实网跑通",
                    "continent": "1. 亚洲法域 (Asia)",
                    "jurisdiction": "1.1 中国大陆 (China Mainland)",
                    "subcategory": "—",
                    "items": [
                        {
                            "detail_url": "https://fgk.chinatax.gov.cn/zcfgk/c100012/c5200001/content.html",
                            "attachment_url": None,
                            "original_file": None,
                            "original_media_type": "text/html",
                            "raw_payload": raw_payload_a,
                            "markdown": markdown_a,
                            "title": "短篇完整法规样本",
                            "doc_number": "税总发〔2026〕1号",
                            "issuer": "国家税务总局",
                            "published_at": "2026-04-01",
                        },
                        {
                            "detail_url": "https://fgk.chinatax.gov.cn/zcfgk/c100012/c5200002/content.html",
                            "attachment_url": None,
                            "original_file": None,
                            "original_media_type": "text/html",
                            "raw_payload": raw_payload_b,
                            "markdown": markdown_b,
                            "title": "长篇截断法规样本",
                            "doc_number": "税总发〔2026〕2号",
                            "issuer": "国家税务总局",
                            "published_at": "2026-04-02",
                        },
                    ],
                }
            ],
            max_items_per_source=10,
        )

        unit_tree: Any = lxml_html.fromstring(rendered_unit)
        unit_rows: list[Any] = unit_tree.xpath('//section[@id="dd-501"]//tr[count(td)=2]')
        assert len(unit_rows) == 2

        payload_text_a = str(
            unit_rows[0].xpath('.//pre[contains(@class, "payload-box")]')[0].text_content() or ""
        ).strip()
        md_text_a = str(
            unit_rows[0].xpath('.//pre[contains(@class, "markdown-box")]')[0].text_content() or ""
        ).strip()
        assert "\n" in payload_text_a, "Case A payload-box must preserve multiple lines"
        assert "…(truncated)" not in payload_text_a, (
            "Case A payload-box must not be prematurely truncated when Markdown is complete"
        )
        assert "…(truncated)" not in md_text_a, (
            "Case A markdown-box must not be truncated when <= 1500 chars"
        )
        assert paragraphs_a[-1] in payload_text_a, (
            "Case A final paragraph of Markdown must also be present at the end of payload-box"
        )

        payload_text_b = str(
            unit_rows[1].xpath('.//pre[contains(@class, "payload-box")]')[0].text_content() or ""
        ).strip()
        md_text_b = str(
            unit_rows[1].xpath('.//pre[contains(@class, "markdown-box")]')[0].text_content() or ""
        ).strip()
        assert payload_text_b.count("\n") >= 5, (
            "Case B payload-box must preserve multi-line formatting (>= 5 newlines)"
        )
        assert payload_text_b.endswith("…(truncated)"), (
            "Case B payload-box must end with '…(truncated)'"
        )
        assert md_text_b.endswith("…(truncated)"), (
            "Case B markdown-box must end with '…(truncated)'"
        )
        payload_body_b = payload_text_b.removesuffix("…(truncated)").strip()
        md_body_b = md_text_b.removesuffix("…(truncated)").strip()
        payload_visible_b = re.sub(r"\s+", "", re.sub(r"<[^>]*>?", "", payload_body_b))
        md_visible_b = re.sub(r"\s+", "", md_body_b)
        assert len(md_visible_b) > 0
        diff_ratio = abs(len(payload_visible_b) - len(md_visible_b)) / len(md_visible_b)
        assert diff_ratio <= 0.15, (
            f"Case B visible text length of payload-box ({len(payload_visible_b)}) must match "
            f"markdown-box ({len(md_visible_b)}) within 15% (got {diff_ratio:.1%})"
        )

        repo_root = Path(__file__).resolve().parents[1]
        samples_html = repo_root / "docs" / "runbooks" / "source-verification-samples.html"
        published_tree: Any = lxml_html.fromstring(samples_html.read_text(encoding="utf-8"))

        all_payload_codes: list[Any] = published_tree.xpath(
            '//pre[contains(@class, "payload-box")]/code'
        )
        assert len(all_payload_codes) > 0
        for idx, code_el in enumerate(all_payload_codes, start=1):
            code_text = str(code_el.text_content() or "").strip()
            assert "\n" in code_text, (
                f"Published payload-box <code> #{idx} is compressed into a single line: {code_text[:80]!r}"
            )

        fulltext_html_sources = (
            "dd-501",
            "dd-040",
            "dd-041",
            "dd-102",
            "dd-375",
            "dd-506",
            "dd-517",
            "dd-520",
            "dd-527",
            "dd-563",
        )
        for src_id in fulltext_html_sources:
            sec_nodes: list[Any] = published_tree.xpath(f'//*[@id="{src_id}"]')
            assert len(sec_nodes) >= 1, f"Missing section #{src_id} in published HTML"
            rows: list[Any] = sec_nodes[0].xpath(".//tr[count(td)=2]")
            if not rows:
                rows = sec_nodes[0].xpath("(./following::table[1])//tr[count(td)=2]")
            assert len(rows) >= 1, f"No sample rows found for {src_id}"
            for r_idx, row in enumerate(rows, start=1):
                p_nodes: list[Any] = row.xpath('.//pre[contains(@class, "payload-box")]')
                m_nodes: list[Any] = row.xpath('.//pre[contains(@class, "markdown-box")]')
                assert len(p_nodes) >= 1 and len(m_nodes) >= 1, (
                    f"{src_id} row #{r_idx} must contain both payload-box and markdown-box"
                )
                p_text = str(p_nodes[0].text_content() or "").strip()
                m_text = str(m_nodes[0].text_content() or "").strip()
                p_trunc = p_text.endswith("…(truncated)")
                m_trunc = m_text.endswith("…(truncated)")
                assert ("…(truncated)" in p_text) == ("…(truncated)" in m_text) and (
                    p_trunc == m_trunc
                ), (
                    f"{src_id} row #{r_idx} truncation mismatch: "
                    f"payload_truncated={p_trunc} (len={len(p_text)}), "
                    f"markdown_truncated={m_trunc} (len={len(m_text)})"
                )



