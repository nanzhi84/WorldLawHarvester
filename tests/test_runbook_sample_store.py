"""Read-only verification of the persistent sample store and DB/CAS HTML export."""

from __future__ import annotations

from pathlib import Path

import psycopg


def _seed_source_samples(
    db_url: str,
    s3_bucket: str,
    source_id: str,
    samples: list[dict[str, object]],
) -> None:
    """Seed self-contained source/run/acquisition/resource/version/document_row + CAS blobs for sample store tests."""
    import hashlib
    import json
    import os
    from datetime import datetime, timezone

    from wlh.storage.cas import Objects

    prev_bucket = os.environ.get("WLH_S3_BUCKET")
    os.environ["WLH_S3_BUCKET"] = s3_bucket
    try:
        objs = Objects()
        with psycopg.connect(db_url, autocommit=True) as conn:
            conn.execute(
                """
                INSERT INTO wlh.source (id, config, generation, run_seq, write_seq)
                VALUES (%s, '{}'::jsonb, 1, 1, 1)
                ON CONFLICT (id) DO NOTHING
                """,
                (source_id,),
            )
            run_id = f"run-{source_id}-seed"
            frontier_id = f"frontier-{source_id}"
            cfg_hash = hashlib.sha256(source_id.encode("utf-8")).hexdigest()
            conn.execute(
                """
                INSERT INTO wlh.run (
                    id, source_id, mode, generation, run_seq, attempt_epoch,
                    deadline, status, config, config_hash, parser_manifest
                )
                VALUES (
                    %s, %s, 'collect', 1, 1, 1,
                    now() + interval '1 hour', 'complete', '{}'::jsonb, %s, '{}'::jsonb
                )
                ON CONFLICT (id) DO NOTHING
                """,
                (run_id, source_id, cfg_hash),
            )
            conn.execute(
                """
                INSERT INTO wlh.frontier (id, source_id, run_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (frontier_id, source_id, run_id),
            )

            for idx, item in enumerate(samples, start=1):
                detail_url = str(item["detail_url"])
                title = str(item["title"])
                raw_bytes = bytes(item["raw_bytes"])  # type: ignore[arg-type]
                mime_type = str(item.get("mime_type") or "text/html; charset=utf-8")
                md_text = str(item["markdown"])
                doc_type = str(item.get("doc_type") or "政策文件")
                doc_number = (
                    str(item["doc_number"]) if item.get("doc_number") is not None else None
                )
                issuer = str(item.get("issuer") or "Official Issuer")
                published_at = str(item.get("published_at") or "2026-03-01")
                effective_at = (
                    str(item["effective_at"])
                    if item.get("effective_at") is not None
                    else "2026-03-15"
                )
                zip_md = str(item["zip_markdown"]) if item.get("zip_markdown") else None

                raw_ref = objs.put(raw_bytes)
                md_ref = objs.put(md_text.encode("utf-8"))
                acq_id = f"acq-{source_id}-{idx}"
                req_key = hashlib.sha256(f"{source_id}:req:{idx}".encode("utf-8")).hexdigest()
                conn.execute(
                    """
                    INSERT INTO wlh.acquisition (
                        id, source_id, run_id, attempt_epoch, request_key,
                        acquisition_kind, role, http_method, request_url, actual_url,
                        mime_type, charset, byte_size, body_hash, archive_ref,
                        state, http_status, outcome, acquired_at
                    )
                    VALUES (
                        %s, %s, %s, 1, %s,
                        'http', 'content', 'GET', %s, %s,
                        %s, 'utf-8', %s, %s, %s,
                        'archived', 200, 'ok', %s
                    )
                    """,
                    (
                        acq_id,
                        source_id,
                        run_id,
                        req_key,
                        detail_url,
                        detail_url,
                        mime_type,
                        len(raw_bytes),
                        raw_ref,
                        raw_ref,
                        datetime.now(timezone.utc),
                    ),
                )

                inputs_bytes = json.dumps(
                    {
                        "inputs": [
                            {
                                "acquisition_id": acq_id,
                                "archive_ref": raw_ref,
                                "charset": "utf-8",
                            }
                        ]
                    }
                ).encode("utf-8")
                inputs_ref = objs.put(inputs_bytes)
                result_bytes = json.dumps({"deliverables": []}).encode("utf-8")
                result_ref = objs.put(result_bytes)

                res_id = f"res-{source_id}-{idx:03d}"
                obs_id = f"obs-{source_id}-{idx:03d}"
                ver_id = f"ver-{source_id}-{idx:03d}"
                fp = hashlib.sha256(f"{source_id}:fp:{idx}".encode("utf-8")).hexdigest()

                with conn.transaction():
                    conn.execute("SET CONSTRAINTS ALL DEFERRED")
                    conn.execute(
                        """
                        INSERT INTO wlh.resource (
                            id, frontier_id, identity_namespace, external_key,
                            public_url, recheck_locator, current_version_id
                        )
                        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                        """,
                        (
                            res_id,
                            frontier_id,
                            source_id,
                            f"ext-{idx}",
                            detail_url,
                            json.dumps({"url": detail_url}),
                            ver_id,
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO wlh.observation (
                            id, resource_id, run_id, attempt_epoch, delivery_key,
                            outcome, change_reason, inputs_ref, result_ref,
                            input_fingerprint, parser_fingerprint, version_fingerprint,
                            content_hash, version_id
                        )
                        VALUES (
                            %s, %s, %s, 1, %s,
                            'created', 'initial', %s, %s,
                            %s, %s, %s,
                            %s, %s
                        )
                        """,
                        (
                            obs_id,
                            res_id,
                            run_id,
                            f"deliv-{source_id}-{idx}",
                            inputs_ref,
                            result_ref,
                            fp,
                            fp,
                            fp,
                            md_ref,
                            ver_id,
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO wlh.version (
                            id, resource_id, sequence, observation_id,
                            result_ref, inputs_ref, input_fingerprint,
                            parser_fingerprint, version_fingerprint, content_hash,
                            parser_config, change_reason
                        )
                        VALUES (
                            %s, %s, 1, %s,
                            %s, %s, %s,
                            %s, %s, %s,
                            '{}'::jsonb, 'initial'
                        )
                        """,
                        (
                            ver_id,
                            res_id,
                            obs_id,
                            result_ref,
                            inputs_ref,
                            fp,
                            fp,
                            fp,
                            md_ref,
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO wlh.document_row (
                            version_id, row_index, title, markdown_file, doc_type,
                            acquired_at, doc_number, issuer, published_at, effective_at,
                            source_name, detail_url, source_record_hash, markdown_hash,
                            role, locator
                        )
                        VALUES (
                            %s, 1, %s, %s, %s,
                            now(), %s, %s, %s::date, %s::date,
                            %s, %s, %s, %s,
                            'primary', %s::jsonb
                        )
                        """,
                        (
                            ver_id,
                            title,
                            f"{source_id}-{idx}.md",
                            doc_type,
                            doc_number,
                            issuer,
                            published_at,
                            effective_at,
                            source_id,
                            detail_url,
                            fp,
                            md_ref,
                            json.dumps({"url": detail_url}),
                        ),
                    )
                    if zip_md is not None:
                        zip_md_ref = objs.put(zip_md.encode("utf-8"))
                        conn.execute(
                            """
                            INSERT INTO wlh.document_row (
                                version_id, row_index, title, markdown_file, doc_type,
                                acquired_at, original_file, doc_number, issuer,
                                published_at, effective_at, source_name, detail_url,
                                source_record_hash, markdown_hash, original_hash,
                                original_media_type, role, locator
                            )
                            VALUES (
                                %s, 2, %s, %s, %s,
                                now(), %s, %s, %s,
                                %s::date, %s::date, %s, %s,
                                %s, %s, %s,
                                'application/zip', 'independent_attachment', %s::jsonb
                            )
                            """,
                            (
                                ver_id,
                                f"{title} (ZIP)",
                                f"{source_id}-{idx}-att.md",
                                doc_type,
                                f"{source_id}-{idx}.zip",
                                doc_number,
                                issuer,
                                published_at,
                                effective_at,
                                source_id,
                                detail_url,
                                fp,
                                zip_md_ref,
                                raw_ref,
                                json.dumps({"url": f"{detail_url}.zip"}),
                            ),
                        )
    finally:
        if prev_bucket is None:
            os.environ.pop("WLH_S3_BUCKET", None)
        else:
            os.environ["WLH_S3_BUCKET"] = prev_bucket


class TestRunbookVerificationSamples:
    def test_unified_test_store_retains_active_sources_and_generates_samples_from_db(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """`DEFAULT_SAMPLES_DATABASE_URL` points to dedicated `wlh_samples` decoupled from `wlh_test`, and `generate_verification_samples_from_db` generates two-column HTML samples for non-Taiwan (`dd-501`) and Taiwan (`dd-578`) sources directly from DB/CAS."""
        import csv
        import importlib
        from typing import Any

        import wlh.runbook_samples as runbook_samples
        from wlh.runbook_samples import generate_verification_samples_from_db

        lxml_html: Any = importlib.import_module("lxml.html")

        default_db_url = getattr(runbook_samples, "DEFAULT_SAMPLES_DATABASE_URL", "")
        default_s3_bucket = getattr(runbook_samples, "DEFAULT_SAMPLES_S3_BUCKET", "")
        assert default_db_url == "postgresql://wlh:wlh@127.0.0.1:15432/wlh_samples"
        assert default_s3_bucket == "wlh-archive-test"
        assert initialized_db["WLH_DATABASE_URL"] != default_db_url

        repo_root = Path(__file__).resolve().parents[1]
        matrix_csv = repo_root / "docs" / "runbooks" / "source-adaptation-matrix.csv"
        assert matrix_csv.is_file(), "Runbook matrix CSV must exist"
        with matrix_csv.open(encoding="utf-8-sig", newline="") as handle:
            rows_by_id = {row["Source ID"].strip(): row for row in csv.DictReader(handle)}

        active_ids = [
            src_id
            for src_id, row in rows_by_id.items()
            if row["状态"].strip().startswith(("[x]", "[~]"))
        ]
        assert len(active_ids) >= 72, "The source registry must identify active sources to verify"

        _seed_source_samples(
            initialized_db["WLH_DATABASE_URL"],
            initialized_db["WLH_S3_BUCKET"],
            "dd-501",
            [
                {
                    "detail_url": f"https://www.gov.cn/gongbao/2026/issue_{i:02d}.htm",
                    "title": f"国务院办公厅关于数字政务协同治理的通知（第{i}号）",
                    "doc_number": f"国办发〔2026〕{10 + i}号",
                    "raw_bytes": (
                        f"<html><body><div class='pages_content'>"
                        f"<h1>国务院办公厅关于数字政务协同治理的通知（第{i}号）</h1>"
                        f"<p>第一条 为推进全国一体化政务大数据体系建设与跨部门协同监管，制定本通知（第{i}号）。</p>"
                        f"</div></body></html>"
                    ).encode("utf-8"),
                    "markdown": (
                        f"# 国务院办公厅关于数字政务协同治理的通知（第{i}号）\n\n"
                        f"第一条 为推进全国一体化政务大数据体系建设与跨部门协同监管，制定本通知（第{i}号）。"
                    ),
                }
                for i in range(1, 6)
            ],
        )
        _seed_source_samples(
            initialized_db["WLH_DATABASE_URL"],
            initialized_db["WLH_S3_BUCKET"],
            "dd-578",
            [
                {
                    "detail_url": f"https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=A0030{i:03d}",
                    "title": f"行政程序法施行細則第{i}編修正條文",
                    "doc_number": f"院臺規字第115000{i:02d}號",
                    "raw_bytes": (
                        f"<html><body><div class='law-reg-content'>"
                        f"<h1>行政程序法施行細則第{i}編修正條文</h1>"
                        f"<p>第 {i} 條 行政機關為行政行為時，應遵循公正、公開與民主之程序規定。</p>"
                        f"</div></body></html>"
                    ).encode("utf-8"),
                    "markdown": (
                        f"# 行政程序法施行細則第{i}編修正條文\n\n"
                        f"第 {i} 條 行政機關為行政行為時，應遵循公正、公開與民主之程序規定。"
                    ),
                }
                for i in range(1, 6)
            ],
        )

        csv_path = tmp_path / "source-adaptation-matrix.csv"
        output_path = tmp_path / "source-verification-samples.html"
        assert not output_path.exists()

        fieldnames = [
            "Source ID",
            "大洲/区域",
            "法域/国家",
            "业务子分类",
            "资料类型",
            "来源名称",
            "官方入口 URL",
            "优先级",
            "推荐适配器",
            "状态",
            "备注与实测结论",
        ]
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for sample_src_id in ("dd-501", "dd-578"):
                src_row = rows_by_id[sample_src_id]
                writer.writerow({k: src_row.get(k, "") for k in fieldnames})

        returned_path = generate_verification_samples_from_db(
            csv_path,
            output_path,
            max_items_per_source=5,
            database_url=initialized_db["WLH_DATABASE_URL"],
            s3_bucket=initialized_db["WLH_S3_BUCKET"],
            fallback_database_url="",
        )
        assert returned_path == output_path
        assert output_path.is_file()

        rendered_html = output_path.read_text(encoding="utf-8")
        assert "<!doctype html>" in rendered_html.lower()
        assert "127.0.0.1" not in rendered_html
        assert "localhost" not in rendered_html

        tree: Any = lxml_html.fromstring(rendered_html)
        for expected_id in ("dd-501", "dd-578"):
            sections: list[Any] = tree.xpath(f'//section[@id="{expected_id}"]')
            assert len(sections) == 1, f"Missing section #{expected_id} in generated HTML"
            sample_rows: list[Any] = sections[0].xpath(".//tr[count(td)=2]")
            assert len(sample_rows) == 5, (
                f"Expected 5 two-column sample rows for {expected_id}, got {len(sample_rows)}"
            )
            for row_idx, row_el in enumerate(sample_rows, start=1):
                tds: list[Any] = row_el.xpath("./td")
                assert len(tds) == 2
                left_text = str(tds[0].text_content() or "").strip()
                right_text = str(tds[1].text_content() or "").strip()
                assert len(left_text) >= 20, (
                    f"{expected_id} row #{row_idx} left cell too short"
                )
                assert len(right_text) >= 20, (
                    f"{expected_id} row #{row_idx} right cell too short"
                )

    def test_generate_samples_falls_back_to_production_store_when_test_store_lacks_source_samples(
        self, initialized_db: dict[str, str], tmp_path: Path, monkeypatch: object
    ) -> None:
        """When a source has 0 document rows in the primary sample store (`dd-582`) or uses zipped CSV archives (`dd-580`), `generate_verification_samples_from_db` automatically falls back to the fallback store and renders unzipped interpretation bodies."""
        import csv
        import importlib
        import io
        import zipfile
        from typing import Any

        from tests.conftest import TRUNCATE_TABLES_SQL
        from wlh.runbook_samples import generate_verification_samples_from_db
        from wlh.storage.db import close_pool, initialize

        lxml_html: Any = importlib.import_module("lxml.html")
        mp: Any = monkeypatch

        primary_db_url = initialized_db["WLH_DATABASE_URL"]
        s3_bucket = initialized_db["WLH_S3_BUCKET"]
        fallback_db_url = "postgresql://wlh:wlh@127.0.0.1:15432/wlh_suite_reconcile_test"

        close_pool()
        with psycopg.connect(
            "postgresql://wlh:wlh@127.0.0.1:15432/postgres", autocommit=True
        ) as admin_conn:
            exists = admin_conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = 'wlh_suite_reconcile_test'"
            ).fetchone()
            if not exists:
                admin_conn.execute("CREATE DATABASE wlh_suite_reconcile_test")

        mp.setenv("WLH_DATABASE_URL", fallback_db_url)
        close_pool()
        initialize()
        with psycopg.connect(fallback_db_url, autocommit=True) as fb_conn:
            fb_conn.execute(TRUNCATE_TABLES_SQL)
        mp.setenv("WLH_DATABASE_URL", primary_db_url)
        close_pool()

        _seed_source_samples(
            fallback_db_url,
            s3_bucket,
            "dd-582",
            [
                {
                    "detail_url": f"https://www.fsc.gov.tw/ch/home.jsp?id=96&dataserno=202603{i:02d}",
                    "title": f"金融監督管理委員會令第1150{i:03d}號",
                    "doc_number": f"金管銀法字第1150{i:03d}號",
                    "raw_bytes": (
                        f"<html><body><div class='main-a_03'>"
                        f"<h1>金融監督管理委員會令第1150{i:03d}號</h1>"
                        f"<p>核釋銀行法第五十條有關法定盈餘公積提列與資本適足率監理規定（第{i}號）。</p>"
                        f"</div></body></html>"
                    ).encode("utf-8"),
                    "markdown": (
                        f"# 金融監督管理委員會令第1150{i:03d}號\n\n"
                        f"核釋銀行法第五十條有關法定盈餘公積提列與資本適足率監理規定（第{i}號）。"
                    ),
                }
                for i in range(1, 11)
            ],
        )

        zip_samples: list[dict[str, object]] = []
        for i in range(1, 6):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(
                    f"interpretation_{i}.json",
                    (
                        f'{{"inte_no": "釋字第{800 + i}號", '
                        f'"inte_issue": "憲法法庭審理案件程序爭點第{i}號", '
                        f'"inte_desc": "大法官解釋主文與理由書全文第{i}號"}}'
                    ),
                )
            zip_samples.append(
                {
                    "detail_url": f"https://cons.judicial.gov.tw/docdata.aspx?fid=38&id=3100{i}",
                    "title": f"司法院大法官解釋與憲法法庭115年憲判字第{i}號",
                    "doc_number": f"115年憲判字第{i}號",
                    "doc_type": "案例",
                    "mime_type": "application/zip",
                    "raw_bytes": buf.getvalue(),
                    "markdown": f"# 司法院大法官解釋與憲法法庭115年憲判字第{i}號\n\n資料集摘要說明第{i}號。",
                    "zip_markdown": (
                        f"| inte_no | inte_issue | inte_desc |\n"
                        f"| --- | --- | --- |\n"
                        f"| 釋字第{800 + i}號 | 憲法法庭審理案件程序爭點第{i}號 | 主文：本件合憲。理由：比例原則審查通過。 |"
                    ),
                }
            )
        _seed_source_samples(fallback_db_url, s3_bucket, "dd-580", zip_samples)

        repo_root = Path(__file__).resolve().parents[1]
        matrix_csv = repo_root / "docs" / "runbooks" / "source-adaptation-matrix.csv"
        assert matrix_csv.is_file(), "Runbook matrix CSV must exist"
        with matrix_csv.open(encoding="utf-8-sig", newline="") as handle:
            rows_by_id = {row["Source ID"].strip(): row for row in csv.DictReader(handle)}

        real_doc_count_sql = """
            SELECT count(DISTINCT r.id)
            FROM wlh.document_row d
            JOIN wlh.version v ON v.id = d.version_id
            JOIN wlh.resource r ON r.id = v.resource_id AND r.current_version_id = v.id
            JOIN wlh.frontier f ON f.id = r.frontier_id
            WHERE f.source_id = 'dd-582'
              AND coalesce(d.detail_url, '') NOT LIKE '%127.0.0.1%'
              AND coalesce(d.detail_url, '') NOT LIKE '%localhost%'
        """
        with psycopg.connect(primary_db_url) as conn:
            row_test = conn.execute(real_doc_count_sql).fetchone()
            test_count = int(row_test[0]) if row_test else 0
        assert test_count == 0, (
            f"Expected 0 non-loopback rows for dd-582 in primary test store, got {test_count}"
        )

        with psycopg.connect(fallback_db_url) as conn:
            row_prod = conn.execute(real_doc_count_sql).fetchone()
            prod_count = int(row_prod[0]) if row_prod else 0
        assert prod_count >= 10, (
            f"Expected >= 10 non-loopback rows for dd-582 in fallback store, got {prod_count}"
        )

        csv_path = tmp_path / "source-adaptation-matrix.csv"
        output_path = tmp_path / "source-verification-samples.html"

        fieldnames = [
            "Source ID",
            "大洲/区域",
            "法域/国家",
            "业务子分类",
            "资料类型",
            "来源名称",
            "官方入口 URL",
            "优先级",
            "推荐适配器",
            "状态",
            "备注与实测结论",
        ]
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for sample_src_id in ("dd-582", "dd-580"):
                src_row = rows_by_id[sample_src_id]
                writer.writerow({k: src_row.get(k, "") for k in fieldnames})

        returned_path = generate_verification_samples_from_db(
            csv_path,
            output_path,
            max_items_per_source=5,
            database_url=primary_db_url,
            s3_bucket=s3_bucket,
            fallback_database_url=fallback_db_url,
            fallback_s3_bucket=s3_bucket,
        )
        assert returned_path == output_path
        assert output_path.is_file()

        rendered_html = output_path.read_text(encoding="utf-8")
        tree: Any = lxml_html.fromstring(rendered_html)

        for expected_id in ("dd-582", "dd-580"):
            sections: list[Any] = tree.xpath(f'//section[@id="{expected_id}"]')
            assert len(sections) == 1, f"Missing section #{expected_id} in generated HTML"
            sample_rows: list[Any] = sections[0].xpath(".//tr[count(td)=2]")
            assert len(sample_rows) == 5, (
                f"Expected 5 two-column sample rows for {expected_id}, got {len(sample_rows)}"
            )

        dd580_rows: list[Any] = tree.xpath('//section[@id="dd-580"]//tr[count(td)=2]')
        for row_idx, row_el in enumerate(dd580_rows, start=1):
            tds: list[Any] = row_el.xpath("./td")
            right_text = str(tds[1].text_content() or "").strip()
            assert any(
                tok in right_text for tok in ("大法官解釋", "憲法法庭", "憲判字")
            ), (
                f"dd-580 row #{row_idx} right cell missing constitutional court header"
            )
            assert any(
                field_tok in right_text
                for field_tok in ("inte_no", "inte_issue", "inte_desc", "判字第", "主文", "理由")
            ), (
                f"dd-580 row #{row_idx} right cell missing constitutional court interpretation/judgment fields"
            )

    def test_isolated_db_sample_generation_renders_anomaly_first_sampling_strata_badges(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """Self-contained isolated DB sample generation selects Anomaly-First Stratified Samples (`p5_shortest`, `p95_longest`, `missing_doc_number`, `missing_effective_at`) without external sample store dependency."""
        import csv
        import importlib
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from typing import Any

        from wlh.runbook_samples import generate_verification_samples_from_db
        from wlh.scheduler.runs import execute_run, prepare_run

        lxml_html: Any = importlib.import_module("lxml.html")
        port_holder: list[int] = []

        class _SampleOriginHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                port = port_holder[0]
                if self.path == "/laws/index.html":
                    body = (
                        f"<html><body><ul>"
                        f'<li><a href="http://127.0.0.1:{port}/laws/doc-1.html">Short Regulation 2026</a></li>'
                        f'<li><a href="http://127.0.0.1:{port}/laws/doc-2.html">Long Omnibus Statute 2026</a></li>'
                        f'<li><a href="http://127.0.0.1:{port}/laws/doc-3.html">Circular Without Document Number</a></li>'
                        f"</ul></body></html>"
                    ).encode("utf-8")
                elif self.path == "/laws/doc-1.html":
                    body = (
                        "<html><body>"
                        '<h1 class="doc-title">Short Regulation 2026</h1>'
                        '<div class="doc-meta"><span class="doc-no">Reg. 2026/01</span>'
                        '<span class="pub-date">2026-01-10</span>'
                        '<span class="eff-date">2026-02-01</span></div>'
                        '<div class="law-body"><p>Article 1. Concise implementing provisions for cross-border customs declaration.</p></div>'
                        "</body></html>"
                    ).encode("utf-8")
                elif self.path == "/laws/doc-2.html":
                    body = (
                        "<html><body>"
                        '<h1 class="doc-title">Long Omnibus Statute 2026</h1>'
                        '<div class="doc-meta"><span class="doc-no">Act 2026/99</span>'
                        '<span class="pub-date">2026-02-15</span></div>'
                        '<div class="law-body">'
                        "<p>Article 1. Comprehensive statutory framework governing financial market infrastructure, central counterparties, and systemic risk oversight across regulated clearing houses.</p>"
                        "<p>Article 2. Prudential capital buffers, default waterfall procedures, and recovery and resolution plans shall be submitted annually to the supervisory authority.</p>"
                        "</div>"
                        "</body></html>"
                    ).encode("utf-8")
                elif self.path == "/laws/doc-3.html":
                    body = (
                        "<html><body>"
                        '<h1 class="doc-title">Circular Without Document Number</h1>'
                        '<div class="doc-meta"><span class="pub-date">2026-03-01</span>'
                        '<span class="eff-date">2026-03-15</span></div>'
                        '<div class="law-body"><p>Article 1. Administrative guidance on electronic filing procedures and digital timestamp verification for public procurement tenders.</p></div>'
                        "</body></html>"
                    ).encode("utf-8")
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _SampleOriginHandler)
        port = srv.server_address[1]
        port_holder.append(port)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            source_yaml = tmp_path / "dd-906.yaml"
            source_yaml.write_text(
                f"""
id: dd-906
scheduler:
  scope: "Isolated sample generation stratified test"
  entry_urls:
    - "http://127.0.0.1:{port}/laws/index.html"
  allowed_hosts:
    - "127.0.0.1"
  allowed_path_prefixes:
    - "/laws/"
fetcher:
  mode: http
  delay: 0.0
  robots: false
processor:
  parser:
    type: html
    documents:
      detail_url_patterns:
        - "^http://127\\\\.0\\\\.0\\\\.1:{port}/laws/doc-\\\\d+\\\\.html$"
      detail_body: always
      content_xpath: '//div[@class="law-body"]'
      fields:
        title:
          xpath: '//h1[@class="doc-title"]'
        document_number:
          xpath: '//span[@class="doc-no"]'
        issuer:
          value: "Ministry of Justice"
        published_at:
          xpath: '//span[@class="pub-date"]'
          regex: '\\d{{4}}-\\d{{2}}-\\d{{2}}'
        effective_at:
          xpath: '//span[@class="eff-date"]'
          regex: '\\d{{4}}-\\d{{2}}-\\d{{2}}'
storage:
  archive_approved: true
  metadata:
    source_name: "Isolated Stratified Portal"
    document_type: "法律法规"
    language: "en"
    jurisdiction: "INTL"
""".strip(),
                encoding="utf-8",
            )
            run_id = prepare_run(source_yaml, "collect")
            res = execute_run(run_id)
            assert res["status"] == "complete"
        finally:
            srv.shutdown()
            thread.join(timeout=2.0)
            srv.server_close()

        with psycopg.connect(initialized_db["WLH_DATABASE_URL"], autocommit=True) as conn:
            conn.execute("ALTER TABLE wlh.document_row DISABLE TRIGGER ALL")
            conn.execute(
                """
                UPDATE wlh.document_row
                SET detail_url = replace(detail_url, %s, 'https://laws.example.org')
                """,
                (f"http://127.0.0.1:{port}",),
            )
            conn.execute("ALTER TABLE wlh.document_row ENABLE TRIGGER ALL")

        csv_path = tmp_path / "isolated-matrix.csv"
        output_path = tmp_path / "isolated-samples.html"
        fieldnames = [
            "Source ID",
            "大洲/区域",
            "法域/国家",
            "业务子分类",
            "资料类型",
            "来源名称",
            "官方入口 URL",
            "优先级",
            "推荐适配器",
            "状态",
            "备注与实测结论",
        ]
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(
                {
                    "Source ID": "dd-906",
                    "大洲/区域": "国际",
                    "法域/国家": "INTL",
                    "业务子分类": "立法",
                    "资料类型": "法律法规",
                    "来源名称": "Isolated Stratified Portal",
                    "官方入口 URL": "https://laws.example.org/laws/index.html",
                    "优先级": "P0",
                    "推荐适配器": "html",
                    "状态": "[x] 已实网跑通",
                    "备注与实测结论": "Isolated DB stratified sampling verification",
                }
            )

        generate_verification_samples_from_db(
            csv_path,
            output_path,
            max_items_per_source=5,
            database_url=initialized_db["WLH_DATABASE_URL"],
            s3_bucket=initialized_db["WLH_S3_BUCKET"],
        )
        rendered = output_path.read_text(encoding="utf-8")
        tree: Any = lxml_html.fromstring(rendered)
        strata_badges = [
            str(el.get("data-stratum") or "")
            for el in tree.xpath('//section[@id="dd-906"]//span[@data-stratum]')
        ]
        assert {"p5_shortest", "p95_longest", "missing_doc_number", "missing_effective_at"}.issubset(
            set(strata_badges)
        ), f"Missing expected stratified badges in isolated HTML: {strata_badges}"


