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
            "vietnam_law",
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
