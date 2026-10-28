"""Read-only verification of the persistent sample store and DB/CAS HTML export."""

from __future__ import annotations

from pathlib import Path

import psycopg


class TestRunbookVerificationSamples:
    def test_unified_test_store_retains_active_sources_and_generates_samples_from_db(
        self, tmp_path: Path
    ) -> None:
        """`wlh_test` and `wlh-archive-test` act as the unified sample store, retain all active sources across `initialized_db` runs, and generate two-column HTML samples for non-Taiwan (`dd-501`) and Taiwan (`dd-578`) sources directly from DB/CAS."""
        import csv
        import importlib
        from typing import Any

        import wlh.runbook_samples as runbook_samples
        from wlh.runbook_samples import generate_verification_samples_from_db

        lxml_html: Any = importlib.import_module("lxml.html")

        default_db_url = getattr(runbook_samples, "DEFAULT_SAMPLES_DATABASE_URL", "")
        default_s3_bucket = getattr(runbook_samples, "DEFAULT_SAMPLES_S3_BUCKET", "")
        assert default_db_url == "postgresql://wlh:wlh@127.0.0.1:15432/wlh_test"
        assert default_s3_bucket == "wlh-archive-test"

        repo_root = Path(__file__).resolve().parents[1]
        matrix_csv = repo_root / "docs" / "runbooks" / "source-adaptation-matrix.csv"
        with matrix_csv.open(encoding="utf-8-sig", newline="") as handle:
            rows_by_id = {row["Source ID"].strip(): row for row in csv.DictReader(handle)}

        active_ids = [
            src_id
            for src_id, row in rows_by_id.items()
            if row["状态"].strip().startswith(("[x]", "[~]"))
        ]
        assert active_ids, "The source registry must identify active sources to verify"

        with psycopg.connect(default_db_url) as conn:
            db_source_ids = {
                str(r[0]) for r in conn.execute("SELECT id FROM wlh.source").fetchall()
            }
        assert set(active_ids).issubset(db_source_ids), (
            f"Active sources missing from unified wlh_test database: {sorted(set(active_ids) - db_source_ids)}"
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
            database_url=default_db_url,
            s3_bucket=default_s3_bucket,
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
                f"Expected 5 two-column sample rows for {expected_id} from wlh_test, got {len(sample_rows)}"
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
