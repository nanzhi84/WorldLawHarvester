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
            if row["状态"].startswith("[x]")
        }
        assert verified_in_csv.issubset(configured_ids), (
            f"Matrix [x] sources missing YAML files: {verified_in_csv - configured_ids}"
        )
