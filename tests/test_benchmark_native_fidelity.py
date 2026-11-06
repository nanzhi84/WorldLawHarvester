"""Independent CLI contracts, specified before production changes.

Complete immutable real inputs plus complete minimal native pages exercise URL,
formatting and legal-date semantics. No model call, network, mocks or extraction
internals; local validation receipts are explicitly not model execution receipts.
Default: 25 minimal cases, without private data. Set WLH_NATIVE_FIDELITY_REAL_DIR
to a private fixture root for all 32 cases; incomplete explicit fixtures fail.
"""

from __future__ import annotations

import copy
import json
import os
import platform
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.benchmark_markdown_support import (
    ROOT,
    freeze_cases,
    isolated_review,
    read_rows,
    reviewed_case,
    sha256,
    validate_receipts,
    write_json,
)

BASE = ROOT / "work/benchmark-expansion-20261106"
ARTIFACTS = ROOT / "artifacts/benchmark-expansion-20261106/native-fidelity-contract"
PENDING = BASE / "native40-pending/pending-native40.jsonl"
PENDING_SHA = "ccb6c3d26e2349f9398cf6a13afc8f53ea4173a18723b6eb9f28feda0d67b336"
NAVIGATION_PENDING = BASE / "gap131-eight-pending/pending-gap131-eight.jsonl"
NAVIGATION_PENDING_SHA = "2c8db16627bab62a601cb8c87c5e88af785de167a983c27aca8e49535abb1fd5"
IMAGE_PENDING = ROOT / "work/native14-pending/pending-native14.jsonl"
IMAGE_PENDING_SHA = "bf4a9e490a1505af9bdbb5fc6e442afe503a248ae5ecbdb8c004192c70b84084"
REAL_IDS = (
    "content_dd-512_cand_424",
    "content_dd-519_cand_06",
    "content_dd-549_cand_26",
    "content_dd-564_cand_620",
    "content_dd-492_03",
    "content_dd-525_cand_497",
    "content_dd-600_cand_821",
)
TITLE = "Native evidence rules"
TAIL = "All complete provisions and original references must remain available without alteration."
FORGED = "Every claimant shall pay a penalty of one million dollars before lodging any appeal."
REVIEWED_TEXT = (
    "Applications may be submitted through the public registry during its opening hours."
)


@dataclass(frozen=True)
class NativeCase:
    name: str
    row: dict[str, Any]
    accepted: bool
    boundary: str


def _copy_real(work: Path, item: dict[str, Any], archive: Path) -> NativeCase:
    source = item["source"]
    ident = source["id"]
    assert sha256(source["raw_payload"].encode()) == source["payload_sha256"]
    assert sha256(archive.read_bytes()) == source["provenance"]["archive_sha256"]
    row = reviewed_case(work, ident, source["raw_payload"], source["mime_type"], "")
    row.update({key: source[key] for key in ("source_id", "jurisdiction", "request_url")})
    row["ground_truth"] = copy.deepcopy(item["proposed_truth"])
    row["review"] = isolated_review(row["payload_sha256"], row["ground_truth"])
    evidence = item.get("review_evidence", item.get("review", {}))
    scope = evidence.get("scope") or " ".join(evidence["page_scope"])
    return NativeCase(ident, row, True, scope)


def _native(
    work: Path,
    name: str,
    source: str,
    markdown: str,
    accepted: bool,
    boundary: str,
    *,
    mime: str = "text/html",
    fields: dict[str, Any] | None = None,
) -> NativeCase:
    raw = (
        source
        if mime == "application/xml"
        else (f"<html><body><article><h1>{TITLE}</h1>{source}<p>{TAIL}</p></article></body></html>")
    )
    row = reviewed_case(work, name, raw, mime, f"# {TITLE}\n\n{markdown}\n\n{TAIL}")
    row["jurisdiction"] = "CA" if mime == "application/xml" else "JP"
    row["ground_truth"]["records"][0].update(title=TITLE, **(fields or {}))
    row["review"] = isolated_review(row["payload_sha256"], row["ground_truth"])
    return NativeCase(name, row, accepted, boundary)


def _url_cases(work: Path) -> list[NativeCase]:
    original = "https://example.gov/files/NQ 46 QH.pdf"
    source = f'<p><a href="{original}">Full text</a></p>'
    markdown = f"[Full text](<{original}>)"
    variants = (
        ("literal-space", original, True),
        ("percent-space", original.replace(" ", "%20"), True),
        ("forged-character", original.replace("46", "47"), False),
        ("forged-host", original.replace("example.gov", "other.example"), False),
        ("forged-protocol", original.replace("https:", "ftp:"), False),
        ("inserted-newline", original.replace("46", "4\n6"), False),
    )
    return [
        _native(
            work,
            f"attachment-{name}",
            source,
            markdown,
            valid,
            "Only native destination or percent-equivalent HTTP(S) reference is supported",
            fields={"attachment_urls": [url]},
        )
        for name, url, valid in variants
    ]


def _body_cases(work: Path) -> list[NativeCase]:
    dl = "<dl><dt>事件名</dt><dd>損害賠償請求事件</dd><dt>結果</dt><dd></dd></dl>"
    table = "| 項目 | 内容 |\n| --- | --- |\n| 事件名 | 損害賠償請求事件 |\n| 結果 | |"
    link = (
        '<a href="/chapter/2120"><div>ARTG2120</div>'
        "<div>Appealing against a decision: contents</div></a>"
    )
    label = "[ARTG2120 Appealing against a decision: contents](/chapter/2120)"
    return [
        _native(work, "generic-metadata-header", dl, table, True, "Generic labels are formatting"),
        _native(
            work,
            "invented-factual-header",
            dl,
            table.replace("項目", FORGED),
            False,
            "A factual claim is not a generic formatting label",
        ),
        _native(
            work,
            "invented-nonempty-result",
            dl,
            table.replace("| 結果 | |", f"| 結果 | {FORGED} |"),
            False,
            "An empty field cannot become an invented result",
        ),
        _native(
            work, "nested-block-link-label", link, label, True, "One link has a complete label"
        ),
        _native(
            work,
            "altered-link-label",
            link,
            label + "\n\n" + FORGED,
            False,
            "A valid destination does not attest unrelated visible claims",
        ),
        *[
            _native(
                work,
                name,
                source,
                REVIEWED_TEXT,
                True,
                "Reviewed scope may use native DOM text regardless of initial source CSS",
            )
            for name, source in (
                ("reviewed-source-hidden-text", f'<p hidden="hidden">{REVIEWED_TEXT}</p>'),
                ("reviewed-source-css-hidden-text", f'<p style="display:none">{REVIEWED_TEXT}</p>'),
            )
        ],
        _native(
            work,
            "description-attribute",
            f'<div data-description="{FORGED}"></div>',
            FORGED,
            False,
            "Arbitrary attributes are not ordinary body text",
        ),
    ]


def _date_cases(work: Path) -> list[NativeCase]:
    date = "<Date><YYYY>2014</YYYY><MM>6</MM><DD>19</DD></Date>"
    assent = f'<Stages stage="assented-to">{date}</Stages>'
    consolidation = (
        '<Stages stage="consolidation"><Date><YYYY>2026</YYYY><MM>9</MM><DD>22</DD></Date></Stages>'
    )
    body = f"<Body><Section><Text>{TAIL}</Text></Section></Body>"
    cases: list[NativeCase] = []
    for name, history, extra, published, valid in (
        ("current-law-assent", assent + consolidation, "", "2014-06-19", True),
        ("consolidation-is-not-assent", assent + consolidation, "", "2026-09-22", False),
        (
            "modified-is-not-assent",
            "",
            "<ModifiedDate>2014-06-19</ModifiedDate>",
            "2014-06-19",
            False,
        ),
        (
            "description-is-not-assent",
            "",
            f"<Description>{date}</Description>",
            "2014-06-19",
            False,
        ),
        (
            "other-law-assent",
            "",
            f"<RelatedDocument><Statute><Identification><ShortTitle>Other Law</ShortTitle>"
            f"<BillHistory>{assent}</BillHistory></Identification></Statute></RelatedDocument>",
            "2014-06-19",
            False,
        ),
        (
            "cross-stage-partial-date",
            '<Stages stage="assented-to"><Date><YYYY>2014</YYYY></Date></Stages>'
            '<Stages stage="consolidation"><Date><MM>6</MM><DD>19</DD></Date></Stages>',
            "",
            "2014-06-19",
            False,
        ),
    ):
        raw = (
            f'<Statute xmlns:lims="http://justice.gc.ca/lims" lims:inforce-start-date="2014-11-01">'
            f"<Identification><ShortTitle>{TITLE}</ShortTitle><BillHistory>{history}</BillHistory>"
            f"</Identification>{body}{extra}</Statute>"
        )
        cases.append(
            _native(
                work,
                name,
                raw,
                "",
                valid,
                "Only complete current-law assent is publication evidence",
                mime="application/xml",
                fields={"published_at": published},
            )
        )
    return cases


def _image_alt_cases(work: Path) -> list[NativeCase]:
    urls = [f"https://example.gov/files/d1795.{ext}" for ext in ("doc", "pdf")]
    icons = [
        f'<a href="{url}"><img src="/icons/{ext}.png" '
        f'alt="Download D17/95 ({ext.upper()}) EN - English"></a>'
        for ext, url in zip(("doc", "pdf"), urls, strict=True)
    ]
    source = "<table><tr><td>" + "\n".join(icons) + "</td></tr></table>"
    unchanged = source.replace("\n", " ")
    return [
        _native(
            work,
            name,
            source,
            markdown,
            accepted,
            "Each business icon retains its own attested accessible name and destination",
            fields={"attachment_urls": urls},
        )
        for name, markdown, accepted in (
            ("adjacent-icon-alt-retained", unchanged, True),
            (
                "adjacent-icon-alt-forged",
                unchanged.replace("EN - English", "FR - French", 1),
                False,
            ),
            ("adjacent-icon-target-forged", unchanged.replace("d1795.doc", "d1895.doc", 1), False),
            (
                "adjacent-icon-image-forged",
                unchanged.replace("/icons/doc.png", "/icons/absent.png", 1),
                False,
            ),
            (
                "adjacent-icon-alt-omitted",
                unchanged.replace('alt="Download D17/95 (DOC) EN - English"', 'alt=""', 1),
                False,
            ),
        )
    ]


@pytest.fixture(scope="session")
def wlh_env() -> dict[str, str]:
    return {}


@pytest.fixture(scope="module")
def fidelity_run() -> dict[str, Any]:
    configured = os.environ.get("WLH_NATIVE_FIDELITY_REAL_DIR")
    originals: dict[str, dict[str, Any]] = {}
    archives: dict[str, Path] = {}
    protected: list[Path] = []
    real_root: Path | None = None
    if configured is not None:
        assert configured.strip(), "WLH_NATIVE_FIDELITY_REAL_DIR must name a fixture root"
        real_root = Path(configured).expanduser().resolve()
        assert real_root.is_dir(), f"Required explicit real fixture root missing: {real_root}"
        for original, digest in (
            (PENDING, PENDING_SHA),
            (NAVIGATION_PENDING, NAVIGATION_PENDING_SHA),
            (IMAGE_PENDING, IMAGE_PENDING_SHA),
        ):
            path = real_root / original.relative_to(ROOT)
            assert path.is_file(), f"Required explicit real fixture missing: {path}"
            assert sha256(path.read_bytes()) == digest
            protected.append(path)
            for item in read_rows(path):
                ident = item["source"]["id"]
                if ident in REAL_IDS:
                    originals[ident] = item
                    archive_sha = item["source"]["provenance"]["archive_sha256"]
                    # Fixed bundles carry old absolute paths; never fall back outside this root.
                    archive = real_root / "work/benchmark-v2-20261105/raw" / archive_sha
                    assert archive.is_file(), f"Required explicit real archive missing: {archive}"
                    archives[ident] = archive
                    protected.append(archive)
        assert set(originals) == set(REAL_IDS)
    work_root = BASE / "native-fidelity-contract"
    work_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=work_root))
    artifacts = ARTIFACTS / work.name
    cases = [
        _copy_real(work, originals[ident], archives[ident])
        for ident in REAL_IDS
        if ident in originals
    ]
    cases += _url_cases(work) + _body_cases(work) + _date_cases(work) + _image_alt_cases(work)
    before = {str(p): sha256(p.read_bytes()) for p in protected}
    write_json(
        artifacts / "environment.json",
        {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "command": ".venv/bin/python -m pytest -q tests/test_benchmark_native_fidelity.py",
            "work": str(work),
            "real_fixture_root": str(real_root) if real_root else None,
            "real_fixture_enabled": configured is not None,
            "pending_sha256": PENDING_SHA,
            "navigation_pending_sha256": NAVIGATION_PENDING_SHA,
            "image_pending_sha256": IMAGE_PENDING_SHA,
            "contract_revision": "v3: native source presence and distinct image alternative text",
            "network": "CLI is file-only; subprocess HTTP(S) proxies target closed loopback port",
            "receipts": "Deterministic local validation fixtures, not fresh model completions",
            "cases": [
                {"id": c.name, "expected_accepted": c.accepted, "boundary": c.boundary}
                for c in cases
            ],
        },
    )
    receipt = validate_receipts([c.row for c in cases], work / "receipts", artifacts)
    accepted = {r["id"] for r in receipt["accepted"]}
    rejected = {r["id"]: r for r in receipt["rejected"]}
    frozen = {
        c.name: freeze_cases([c.row], work / "freeze" / c.name, artifacts / "freeze" / c.name)
        for c in cases
    }
    assertions = [
        {
            "id": c.name,
            "expected_accepted": c.accepted,
            "receipt_accepted": c.name in accepted,
            "freeze_accepted": frozen[c.name]["returncode"] == 0,
            "reasons": rejected.get(c.name, {}).get("reasons", []),
            "passed": (c.name in accepted) == c.accepted
            and (frozen[c.name]["returncode"] == 0) == c.accepted,
        }
        for c in cases
    ]
    unchanged = all(sha256(Path(p).read_bytes()) == digest for p, digest in before.items())
    write_json(
        artifacts / "assertion-results.json",
        {
            "cases": assertions,
            "protected_inputs_unchanged": unchanged,
            "protected_before": before,
            "pass": all(r["passed"] for r in assertions),
        },
    )
    write_json(ARTIFACTS / "latest.json", {"artifacts": str(artifacts), "work": str(work)})
    return {
        "real_fixture_enabled": configured is not None,
        "cases": cases,
        "receipt": receipt,
        "freeze": frozen,
        "assertions": assertions,
        "unchanged": unchanged,
    }


class TestBenchmarkNativeFidelity:
    @pytest.mark.parametrize("entrypoint", ("receipt", "freeze"))
    def test_complete_real_native_truth_is_accepted_without_field_deletion(
        self,
        fidelity_run: dict[str, Any],
        entrypoint: str,
    ) -> None:
        if not fidelity_run["real_fixture_enabled"]:
            pytest.skip("Set WLH_NATIVE_FIDELITY_REAL_DIR to the complete private fixture root")
        failures = [
            r
            for r in fidelity_run["assertions"]
            if r["id"] in REAL_IDS and not r[f"{entrypoint}_accepted"]
        ]
        assert not failures, json.dumps(failures, ensure_ascii=False, indent=2)

    @pytest.mark.parametrize("entrypoint", ("receipt", "freeze"))
    def test_native_formatting_is_accepted_and_unattested_claims_are_rejected(
        self,
        fidelity_run: dict[str, Any],
        entrypoint: str,
    ) -> None:
        failures = [
            r
            for r in fidelity_run["assertions"]
            if r["id"] not in REAL_IDS and r[f"{entrypoint}_accepted"] != r["expected_accepted"]
        ]
        assert not failures, json.dumps(failures, ensure_ascii=False, indent=2)

    def test_originals_receipts_and_frozen_exports_preserve_complete_content(
        self,
        fidelity_run: dict[str, Any],
    ) -> None:
        assert fidelity_run["unchanged"]
        receipt = fidelity_run["receipt"]
        assert receipt["inputs_unchanged"] and receipt["raw_receipts_unchanged"]
        for result in fidelity_run["freeze"].values():
            assert result["inputs_unchanged"]
            if result["returncode"] == 0:
                assert result["published"] and result["complete_payload_and_truth_preserved"]
            else:
                assert not result["published"] and not result["exported"]
