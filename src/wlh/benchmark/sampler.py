"""Representative candidate sample extractor for quantitative benchmarking.

Extracts stratified candidate items from `docs/runbooks/source-verification-samples.html`
and `docs/runbooks/source-adaptation-matrix.csv`, preserving raw payload and extracted fields.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence, cast
from urllib.parse import urlsplit, urlunsplit

from wlh.benchmark.models import (
    ContentPageGroundTruth,
    ListPageGroundTruth,
    ProcessorBenchmarkTask,
    RecordGroundTruth,
)

DEFAULT_HTML_PATH = Path("docs/runbooks/source-verification-samples.html")
DEFAULT_CSV_PATH = Path("docs/runbooks/source-adaptation-matrix.csv")
DEFAULT_OUTPUT_DIR = Path("data/benchmark")

# Core Stratified Candidate Plan covering 14 jurisdictions, 6 strata, and M1 defects
_CORE_CANDIDATE_PLAN: tuple[tuple[str, int, str, str], ...] = (
    # 1. Known Defect Challenges from M1 Survey (4 items)
    (
        "dd-501",
        0,
        "CN",
        "M1 Defect Challenge: Departmental order in State Council Gazette where issuer was "
        "mistakenly attributed to portal owner 国务院 instead of authoring agency "
        "国家发展和改革委员会, effective date in body text (自2026年10月1日起施行) was omitted in "
        "metadata (null), and publication date conflicted with ministerial signing date; "
        "p5_shortest strata.",
    ),
    (
        "dd-520",
        0,
        "JP",
        "M1 Defect Challenge: National Tax Agency circular where document number (課法2-1等) "
        "and issuance date were nested in the header table and missed by content extraction rules, "
        "causing missing_doc_number; p5_shortest strata.",
    ),
    (
        "dd-572",
        0,
        "ZA",
        "M1 Defect Challenge: Western Cape Provincial Gazette by-law where doc_number field was "
        "polluted with file size metadata ('Western Cape Provincial Gazette 9196 on 18 February "
        "2026 (372.7 KB)'); missing effective date; p5_shortest strata.",
    ),
    (
        "dd-502",
        0,
        "CN",
        "M1 Defect Challenge: Historical revision document number misattribution where scraper "
        "captured the legacy repealed decree (河南省人民政府令第128号) instead of the latest revised "
        "decree (河南省人民政府令第238号); missing effective date; p5_shortest strata.",
    ),
    # 2. Quality Warning Routes (accept_with_warning) (3 items)
    (
        "dd-519",
        2,
        "JP",
        "Quality warning route (accept_with_warning): judicial civil precedent triggering reading "
        "order instability or OCR low-ink warning; missing effective date.",
    ),
    (
        "dd-519",
        6,
        "JP",
        "Quality warning route (accept_with_warning): judicial damages precedent passing quality "
        "gate with warning flags; missing effective date.",
    ),
    (
        "dd-519",
        9,
        "JP",
        "Quality warning route (accept_with_warning): multi-count criminal judgment from Japanese "
        "courts triggering warning classification; missing effective date.",
    ),
    # 3. Standard Clean Baselines (5 items)
    (
        "dd-499",
        2,
        "US",
        "Standard baseline regulation from US Federal Register with fully populated doc_number, "
        "published_at, and effective_at.",
    ),
    (
        "dd-499",
        3,
        "US",
        "Standard baseline state regulatory program approval from US Federal Register with complete "
        "baseline metadata without anomalies.",
    ),
    (
        "dd-493",
        4,
        "VN",
        "Standard baseline Vietnamese ministerial circular with clean metadata (doc_number, "
        "published_at, effective_at).",
    ),
    (
        "dd-514",
        2,
        "VN",
        "Standard baseline Vietnamese Supreme Court judicial precedent with fully validated "
        "metadata.",
    ),
    (
        "dd-492",
        4,
        "VN",
        "Standard baseline Vietnamese provincial government decision with complete baseline "
        "metadata.",
    ),
    # 4. Long Comprehensive Documents (p95_longest) (6 items)
    (
        "dd-501",
        1,
        "CN",
        "Comprehensive departmental regulation (p95_longest): Ministry of Culture and Tourism "
        "Order No. 15 with 40+ articles; missing effective date in metadata.",
    ),
    (
        "dd-558",
        1,
        "US",
        "Massive statutory title (p95_longest): US Code Title 7 (Agriculture) testing long-form "
        "document completeness and truncation detection.",
    ),
    (
        "dd-550",
        1,
        "DE",
        "Comprehensive German federal environmental regulation (BImSchV 1, p95_longest) with "
        "extensive technical provisions and annexes.",
    ),
    (
        "dd-509",
        1,
        "TW",
        "Comprehensive Taiwan statutory code (Administrative Appeal Act, p95_longest) testing "
        "structural article completeness.",
    ),
    (
        "dd-548",
        1,
        "GB",
        "Comprehensive UK Upper Tribunal tax decision (p95_longest) with extensive legal reasoning "
        "and complex multi-page structure.",
    ),
    (
        "dd-572",
        1,
        "ZA",
        "Long South African municipal code (p95_longest): Community Fire Safety By-law with file "
        "size pollution in doc_number ((3.5 MB)).",
    ),
    # 5. Short Documents & Missing Doc Number & Multi-Jurisdiction Diversity (10 items)
    (
        "dd-525",
        0,
        "SG",
        "Singapore statutory act (Accounting Standards Act 2007): missing official doc_number "
        "from portal listing; p5_shortest text.",
    ),
    (
        "dd-556",
        0,
        "CH",
        "Swiss Federal Supreme Court concise judicial order (5A_959/2026); German-language civil "
        "execution case; p5_shortest text.",
    ),
    (
        "dd-570",
        0,
        "NZ",
        "New Zealand statutory amendment act (Healthy Futures (Pae Ora) Act 2026 No 74); "
        "p5_shortest text with missing effective date in metadata.",
    ),
    (
        "dd-506",
        0,
        "HK",
        "Hong Kong Court of Appeal civil judgment (CACV 382/2024); common law judicial decision; "
        "p5_shortest text; missing effective date.",
    ),
    (
        "dd-019",
        0,
        "CN",
        "Supreme People's Court of China judicial reply without formal doc_number; p5_shortest text.",
    ),
    (
        "dd-132",
        0,
        "TW",
        "Taiwan Intellectual Property Office administrative guidelines announcement missing "
        "official doc_number; p5_shortest text.",
    ),
    (
        "dd-551",
        0,
        "DE",
        "German Federal Court of Justice (BGH 2 ARs 569/09) juvenile jurisdiction decision; "
        "concise procedural ruling; p5_shortest text.",
    ),
    (
        "dd-549",
        0,
        "GB",
        "UK HMRC internal manual page on VAT appeals procedure (ARTG2100); concise guidance page; "
        "p5_shortest text.",
    ),
    (
        "dd-511",
        0,
        "TW",
        "Taiwan Ministry of Finance tax circular (台財稅第841660961號); concise commodity tax "
        "interpretation; p5_shortest text.",
    ),
    (
        "dd-499",
        1,
        "US",
        "Comprehensive federal regulatory document from US Federal Register (p95_longest) with "
        "populated effective date.",
    ),
    # 6. Full Adapted Sources Representative Coverage (51 items across remaining sources)
    (
        "dd-017",
        0,
        "CN",
        "Adapted source representative sample (dd-017): 最高人民法院指导案例 (案例); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-039",
        0,
        "CN",
        "Adapted source representative sample (dd-039): 最高人民检察院官网 (政策文件); strata: p5_shortest, missing_doc_number, missing_effective_at.",
    ),
    (
        "dd-040",
        0,
        "CN",
        "Adapted source representative sample (dd-040): 最高人民检察院公报 (政策文件); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-041",
        0,
        "CN",
        "Adapted source representative sample (dd-041): 最高人民检察院指导性案例 (案例); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-102",
        0,
        "CN",
        "Adapted source representative sample (dd-102): 外汇局（行政处罚与监管信息公开） (通知公告); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-108",
        0,
        "CN",
        "Adapted source representative sample (dd-108): 信息通信行业信用信息查询 (通知公告); strata: p5_shortest, missing_doc_number, missing_effective_at.",
    ),
    (
        "dd-142",
        0,
        "KR",
        "Adapted source representative sample (dd-142): 韩国特许厅（KIPO） (其他); strata: p5_shortest, missing_doc_number, missing_effective_at.",
    ),
    (
        "dd-143",
        0,
        "JP",
        "Adapted source representative sample (dd-143): 日本专利信息平台 (其他); strata: p5_shortest, missing_doc_number, missing_effective_at.",
    ),
    (
        "dd-153",
        0,
        "CN",
        "Adapted source representative sample (dd-153): 中国互联网信息中心 (其他); strata: p5_shortest, missing_doc_number, missing_effective_at.",
    ),
    (
        "dd-162",
        0,
        "TW",
        "Adapted source representative sample (dd-162): 台湾证券交易所 (财务报告); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-183",
        3,
        "CN",
        "Adapted source representative sample (dd-183): 地方债管理 (政策文件); strata: missing_effective_at.",
    ),
    (
        "dd-198",
        0,
        "CN",
        "Adapted source representative sample (dd-198): 中国企业国有产权交易机构协会 (通知公告); strata: p5_shortest, missing_doc_number, missing_effective_at.",
    ),
    (
        "dd-209",
        0,
        "CN",
        "Adapted source representative sample (dd-209): 广州产权交易所 (通知公告); strata: p5_shortest, missing_doc_number, missing_effective_at.",
    ),
    (
        "dd-265",
        1,
        "CN",
        "Adapted source representative sample (dd-265): 中国注册税务师协会 (税务资料); strata: p95_longest, missing_effective_at.",
    ),
    (
        "dd-320",
        3,
        "CN",
        "Adapted source representative sample (dd-320): 山西自然资源厅/局（政务与审批公开） (通知公告); strata: missing_effective_at.",
    ),
    (
        "dd-323",
        3,
        "CN",
        "Adapted source representative sample (dd-323): 吉林自然资源厅/局（政务与审批公开） (通知公告); strata: missing_effective_at.",
    ),
    (
        "dd-324",
        0,
        "CN",
        "Adapted source representative sample (dd-324): 黑龙江自然资源厅/局（政务与审批公开） (通知公告); strata: p5_shortest, missing_doc_number, missing_effective_at.",
    ),
    (
        "dd-327",
        0,
        "CN",
        "Adapted source representative sample (dd-327): 浙江自然资源厅/局（政务与审批公开） (通知公告); strata: p5_shortest, missing_doc_number, missing_effective_at.",
    ),
    (
        "dd-337",
        0,
        "CN",
        "Adapted source representative sample (dd-337): 海南自然资源厅/局（政务与审批公开） (通知公告); strata: p5_shortest, missing_doc_number, missing_effective_at.",
    ),
    (
        "dd-375",
        0,
        "CN",
        "Adapted source representative sample (dd-375): 国民经济行业分类 (行业标准); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-385",
        0,
        "CN",
        "Adapted source representative sample (dd-385): 国家级高新技术产业开发区名单 (通知公告); strata: p5_shortest, missing_doc_number, missing_effective_at.",
    ),
    (
        "dd-423",
        0,
        "CN",
        "Adapted source representative sample (dd-423): [民航局]资质查询 (通知公告); strata: p5_shortest, missing_doc_number, missing_effective_at.",
    ),
    (
        "dd-448",
        0,
        "CN",
        "Adapted source representative sample (dd-448): 电影备案公示 (通知公告); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-455",
        0,
        "CN",
        "Adapted source representative sample (dd-455): 电子游戏审批 (通知公告); strata: p5_shortest, missing_doc_number, missing_effective_at.",
    ),
    (
        "dd-482",
        1,
        "CN",
        "Adapted source representative sample (dd-482): 中国政府采购网 (通知公告); strata: p95_longest, missing_effective_at.",
    ),
    (
        "dd-495",
        0,
        "VN",
        "Adapted source representative sample (dd-495): LuatVietnam 法规库 (法律法规); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-496",
        0,
        "VN",
        "Adapted source representative sample (dd-496): LuatVietnam 判决库 (案例); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-507",
        0,
        "HK",
        "Adapted source representative sample (dd-507): 香港税务局（释义及执行指引） (税务资料); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-510",
        0,
        "TW",
        "Adapted source representative sample (dd-510): 司法院法学资料检索系统（裁判书查询） (案例); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-512",
        0,
        "VN",
        "Adapted source representative sample (dd-512): 越南国会法律文书系统（Văn bản quy phạm pháp luật Quốc hội） (法律法规); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-513",
        0,
        "VN",
        "Adapted source representative sample (dd-513): 越南政府门户政策与法规建设专栏（Xây dựng chính sách, pháp luật） (法律法规); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-516",
        0,
        "VN",
        "Adapted source representative sample (dd-516): 越南财政部 / 税务总局法规与政策解答库（Bộ Tài chính / Tổng cục Thuế） (税务资料); strata: p5_shortest, missing_doc_number, missing_effective_at.",
    ),
    (
        "dd-517",
        0,
        "JP",
        "Adapted source representative sample (dd-517): e-Gov 法令检索 / 法令 API v2（デジタル庁） (法律法规); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-518",
        0,
        "JP",
        "Adapted source representative sample (dd-518): 互联网版官报（インターネット版官報） (政策文件); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-527",
        0,
        "SG",
        "Adapted source representative sample (dd-527): 新加坡税局（IRAS）（e-Tax Guides） (税务资料); strata: p5_shortest, missing_doc_number, missing_effective_at.",
    ),
    (
        "dd-557",
        0,
        "US",
        "Adapted source representative sample (dd-557): GovInfo (U.S. Government Publishing Office API & Bulk Data) (法律法规); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-559",
        0,
        "US",
        "Adapted source representative sample (dd-559): 电子联邦法规汇编（eCFR API - ecfr.gov） (法律法规); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-560",
        0,
        "US",
        "Adapted source representative sample (dd-560): Open States 美国 50 州立法与法案聚合 (法律法规); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-561",
        0,
        "US",
        "Adapted source representative sample (dd-561): CourtListener / Free Law Project & Juriscraper（美国联邦与各州判例） (案例); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-562",
        0,
        "US",
        "Adapted source representative sample (dd-562): 美国联邦最高法院官网（supremecourt.gov）与 Caselaw Access Project (CAP) (案例); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-563",
        0,
        "US",
        "Adapted source representative sample (dd-563): 美国国税局（IRS Internal Revenue Bulletins / Written Determinations）与美国税务法院（DAWSON） (税务资料); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-564",
        0,
        "CA",
        "Adapted source representative sample (dd-564): 加拿大司法部联邦法规库（Justice Laws Website & GitHub XML） (法律法规); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-565",
        0,
        "CA",
        "Adapted source representative sample (dd-565): 加拿大最高法院（SCC / Lexum）、联邦法院与 CanLII (案例); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-571",
        0,
        "NZ",
        "Adapted source representative sample (dd-571): 新西兰司法部判决库（NZLII / Courts of NZ）与税务局税务政策（IRD Tax Policy） (税务资料); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-575",
        0,
        "VN",
        "Adapted source representative sample (dd-575): 越南国会法律草案征求意见门户（Dự thảo online） (法律法规); strata: p5_shortest, missing_doc_number, missing_effective_at.",
    ),
    (
        "dd-577",
        0,
        "TW",
        "Adapted source representative sample (dd-577): 行政院公报资讯网（Executive Yuan Gazette） (政策文件); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-578",
        0,
        "TW",
        "Adapted source representative sample (dd-578): 全国法规资料库中文法规 XML（政府资料开放平臺 18289） (法律法规); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-579",
        0,
        "TW",
        "Adapted source representative sample (dd-579): 全国法规资料库中文命令 XML（政府资料开放平臺 18290） (法律法规); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-580",
        0,
        "TW",
        "Adapted source representative sample (dd-580): 司法院资料开放平台（Judicial Yuan Open Data API） (案例); strata: p5_shortest, missing_effective_at.",
    ),
    (
        "dd-582",
        1,
        "JP",
        "Adapted source representative sample (dd-582): 国税不服审判所公表裁决事例（KFS） (税务资料); strata: p95_longest, missing_doc_number, missing_effective_at.",
    ),
    (
        "dd-591",
        0,
        "TW",
        "Adapted source representative sample (dd-591): 全国法规资料库（司法解释：最高法院与最高行政法院判例） (案例); strata: p5_shortest, missing_effective_at.",
    ),
)

_JUR_PREFIX_MAP: dict[str, str] = {
    "1.1": "CN",
    "1.2": "HK",
    "1.3": "TW",
    "1.4": "VN",
    "1.5": "JP",
    "1.6": "KR",
    "1.7": "SG",
    "2.2": "GB",
    "2.3": "DE",
    "2.5": "CH",
    "3.1": "US",
    "3.2": "CA",
    "4.2": "NZ",
    "5.1": "ZA",
}



def load_cached_samples(html_path: Path) -> dict[str, list[dict[str, Any]]]:
    """Load cached sample items per `source_id` from the verification HTML file."""
    if not html_path.is_file():
        raise FileNotFoundError(f"HTML verification sample file not found: {html_path}")
    text = html_path.read_text(encoding="utf-8")
    m = re.search(
        r'<script\s+id="wlh-samples-cache"\s+type="application/json">(.*?)</script>',
        text,
        flags=re.S,
    )
    if not m:
        raise ValueError(f"No #wlh-samples-cache script found in {html_path}")
    raw_data: Any = json.loads(m.group(1))
    if not isinstance(raw_data, dict):
        raise ValueError(f"Unexpected JSON data type in #wlh-samples-cache: {type(raw_data)}")
    out: dict[str, list[dict[str, Any]]] = {}
    for k, v in cast(dict[str, Any], raw_data).items():
        if isinstance(v, list):
            out[k] = [
                cast(dict[str, Any], item)
                for item in cast(list[Any], v)
                if isinstance(item, Mapping)
            ]
    return out


def load_source_metadata(csv_path: Path) -> dict[str, dict[str, str]]:
    """Load source metadata from source-adaptation-matrix.csv."""
    if not csv_path.is_file():
        raise FileNotFoundError(f"Source adaptation matrix CSV not found: {csv_path}")
    metadata: dict[str, dict[str, str]] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            source_id = (row.get("Source ID") or "").strip()
            if source_id:
                metadata[source_id] = {
                    "source_name": (row.get("来源名称") or "").strip(),
                    "jurisdiction_name": (row.get("法域/国家") or "").strip(),
                    "region": (row.get("大洲/区域") or "").strip(),
                    "doc_type": (row.get("资料类型") or "").strip(),
                    "priority": (row.get("优先级") or "").strip(),
                }
    return metadata


def _build_full_runbook_plan(
    html_path: Path = DEFAULT_HTML_PATH,
    csv_path: Path = DEFAULT_CSV_PATH,
) -> tuple[tuple[str, int, str, str], ...]:
    """Expand _CORE_CANDIDATE_PLAN to all verification samples in the runbook HTML cache."""
    if not html_path.is_file() or not csv_path.is_file():
        return _CORE_CANDIDATE_PLAN
    try:
        cached = load_cached_samples(html_path)
        meta = load_source_metadata(csv_path)
    except Exception:
        return _CORE_CANDIDATE_PLAN

    allowed_sids: set[str] | None = None
    raw_cands_path = DEFAULT_OUTPUT_DIR / "raw_candidates.jsonl"
    if html_path == DEFAULT_HTML_PATH and raw_cands_path.is_file():
        try:
            allowed_sids = {
                str(json.loads(line)["source_id"])
                for line in raw_cands_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            }
        except Exception:
            allowed_sids = None

    seen: set[tuple[str, int]] = {(sid, sidx) for sid, sidx, _, _ in _CORE_CANDIDATE_PLAN}
    expanded: list[tuple[str, int, str, str]] = list(_CORE_CANDIDATE_PLAN)

    for sid in sorted(cached.keys()):
        if allowed_sids is not None and sid not in allowed_sids:
            continue
        smeta = meta.get(sid, {})
        jname = smeta.get("jurisdiction_name", "")
        prefix = jname.split(" ")[0] if " " in jname else ""
        jur_code = _JUR_PREFIX_MAP.get(prefix, "CN")
        sname = smeta.get("source_name") or sid
        dtype = smeta.get("doc_type") or "其他"
        for s_idx, item in enumerate(cached[sid]):
            if (sid, s_idx) in seen:
                continue
            seen.add((sid, s_idx))
            strata = ", ".join(str(s) for s in item.get("sampling_strata", []) if str(s).strip())
            reason = (
                f"Runbook verification sample ({sid}[{s_idx}]): {sname} ({dtype})"
                + (f"; strata: {strata}." if strata else ".")
            )
            expanded.append((sid, s_idx, jur_code, reason))

    return tuple(expanded)


CANDIDATE_PLAN: tuple[tuple[str, int, str, str], ...] = _build_full_runbook_plan()


def extract_representative_candidates(
    html_path: Path = DEFAULT_HTML_PATH,
    csv_path: Path = DEFAULT_CSV_PATH,
    plan: Sequence[tuple[str, int, str, str]] = CANDIDATE_PLAN,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract representative candidate items from verification samples based on the plan.

    Returns:
        tuple of (candidates_list, manifest_dict)
    """
    cached_data = load_cached_samples(html_path)
    needs_hydration = any(
        not str(item.get("raw_payload") or item.get("markdown") or "").strip()
        or str(item.get("raw_payload") or "").strip().endswith("…(truncated)")
        for items in cached_data.values()
        for item in items
    )
    if needs_hydration:
        from wlh.runbook_samples import hydrate_samples_from_store

        try:
            cached_data = hydrate_samples_from_store(
                cached_data, full=True, csv_path=csv_path
            )
        except Exception:
            local_jsonl = DEFAULT_OUTPUT_DIR / "raw_candidates.jsonl"
            if local_jsonl.exists():
                by_id: dict[str, dict[str, Any]] = {}
                with local_jsonl.open("r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            row = json.loads(line)
                            by_id[str(row.get("id", ""))] = row
                for idx, (sid, s_idx, _, _) in enumerate(CANDIDATE_PLAN, start=1):
                    cid = f"cand_{idx:02d}"
                    if (
                        cid in by_id
                        and sid in cached_data
                        and s_idx < len(cached_data[sid])
                    ):
                        target = cached_data[sid][s_idx]
                        if not target.get("raw_payload"):
                            target["raw_payload"] = by_id[cid].get("raw_payload", "")
                        if not target.get("markdown"):
                            target["markdown"] = by_id[cid].get("markdown", "")
    source_meta = load_source_metadata(csv_path)

    candidates: list[dict[str, Any]] = []
    manifest_entries: list[dict[str, Any]] = []

    strata_counts: dict[str, int] = {}
    doc_type_counts: dict[str, int] = {}
    jurisdiction_set: set[str] = set()

    for idx, (source_id, sample_idx, jurisdiction, reason) in enumerate(plan, start=1):
        cand_id = f"cand_{idx:02d}"
        if source_id not in cached_data:
            raise KeyError(f"Source ID {source_id} not found in HTML samples cache")
        samples_for_source = cached_data[source_id]
        if sample_idx >= len(samples_for_source):
            raise IndexError(
                f"Sample index {sample_idx} out of range for {source_id} "
                f"(has {len(samples_for_source)} samples)"
            )

        raw_item = samples_for_source[sample_idx]
        smeta = source_meta.get(source_id, {})

        # Ensure raw_payload is non-empty (fall back to markdown for PDF-native sources)
        raw_payload = str(raw_item.get("raw_payload") or raw_item.get("markdown") or "").strip()
        if not raw_payload:
            raise ValueError(f"Candidate {cand_id} ({source_id}[{sample_idx}]) has empty raw_payload")

        # Resolve strata and route
        sampling_strata = [
            str(s) for s in raw_item.get("sampling_strata", []) if str(s).strip()
        ]
        quality_route = str(raw_item.get("quality_route") or "accept").strip()

        doc_type = smeta.get("doc_type") or "其他"
        jurisdiction_name = smeta.get("jurisdiction_name") or jurisdiction
        source_name = smeta.get("source_name") or source_id

        # Update statistics
        jurisdiction_set.add(jurisdiction)
        doc_type_counts[doc_type] = doc_type_counts.get(doc_type, 0) + 1
        for stratum in sampling_strata:
            strata_counts[stratum] = strata_counts.get(stratum, 0) + 1

        candidate_record: dict[str, Any] = {
            "id": cand_id,
            "source_id": source_id,
            "source_name": source_name,
            "jurisdiction": jurisdiction,
            "jurisdiction_name": jurisdiction_name,
            "doc_type": doc_type,
            "title": raw_item.get("title"),
            "doc_number": raw_item.get("doc_number"),
            "published_at": raw_item.get("published_at"),
            "effective_at": raw_item.get("effective_at"),
            "issuer": raw_item.get("issuer"),
            "detail_url": raw_item.get("detail_url") or "",
            "attachment_url": raw_item.get("attachment_url"),
            "attachments": raw_item.get("attachments") or [],
            "quality_route": quality_route,
            "sampling_strata": sampling_strata,
            "selection_reason": reason,
            "markdown": str(raw_item.get("markdown") or "").strip(),
            "raw_payload": raw_payload,
        }
        candidates.append(candidate_record)

        manifest_entry: dict[str, Any] = {
            "id": cand_id,
            "source_id": source_id,
            "source_name": source_name,
            "jurisdiction": jurisdiction,
            "jurisdiction_name": jurisdiction_name,
            "doc_type": doc_type,
            "title": raw_item.get("title"),
            "doc_number": raw_item.get("doc_number"),
            "published_at": raw_item.get("published_at"),
            "effective_at": raw_item.get("effective_at"),
            "issuer": raw_item.get("issuer"),
            "quality_route": quality_route,
            "sampling_strata": sampling_strata,
            "selection_reason": reason,
            "raw_payload_length": len(raw_payload),
            "markdown_length": len(candidate_record["markdown"]),
        }
        manifest_entries.append(manifest_entry)

    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "metadata": {
            "total_candidates": len(candidates),
            "total_jurisdictions": len(jurisdiction_set),
            "jurisdictions": sorted(jurisdiction_set),
            "strata_distribution": strata_counts,
            "doc_type_distribution": doc_type_counts,
            "m1_defect_challenges_included": ["dd-501", "dd-520", "dd-572", "dd-502"],
            "source_html_path": str(html_path),
            "source_csv_path": str(csv_path),
            "extracted_at": datetime.now(timezone.utc).isoformat(),
        },
        "candidates": manifest_entries,
    }

    return candidates, manifest


def write_benchmark_candidates(
    output_dir: Path,
    candidates: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> None:
    """Write candidates to raw_candidates.jsonl and candidate_manifest.json."""
    output_dir.mkdir(parents=True, exist_ok=True)

    jsonl_path = output_dir / "raw_candidates.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for item in candidates:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    manifest_path = output_dir / "candidate_manifest.json"
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def export_processor_batch_bundle(
    output_path: Path | str = DEFAULT_OUTPUT_DIR / "processor_batch_bundle.jsonl",
    fixtures_dir: Path | str = Path("tests/fixtures"),
    candidates_path: Path | str = DEFAULT_OUTPUT_DIR / "raw_candidates.jsonl",
    csv_path: Path | str = DEFAULT_CSV_PATH,
    max_list_per_source: int = 1,
    max_content_per_source: int = 10,
    max_char_length: int = 100_000,
) -> list[ProcessorBenchmarkTask]:
    """Export 100% untruncated HTML/JSON/XML batch bundle covering list pages and content pages.

    Excludes pure binaries (PDF/Office/ZIP/images). Deduplicates bulk containers by (source_id, payload_sha256).
    Applies per-source caps (max 1 representative list page + up to 10 content pages per source).
    """
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    fix_dir = Path(fixtures_dir)
    source_metadata = load_source_metadata(Path(csv_path)) if Path(csv_path).is_file() else {}

    list_tasks_by_source: dict[str, list[ProcessorBenchmarkTask]] = {}
    content_tasks_by_source: dict[str, list[ProcessorBenchmarkTask]] = {}
    seen_payload_hashes: set[tuple[str, str]] = set()

    BINARY_EXTENSIONS = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".zip", ".png", ".jpg", ".jpeg", ".bin"}
    BINARY_PREFIXES = (b"%PDF-", b"PK\x03\x04", b"\xd0\xcf\x11\xe0", b"\x89PNG")

    # 1. Scan tests/fixtures/dd-*/manifest.json
    manifest_paths = sorted(fix_dir.glob("dd-*/manifest.json"))
    for mf in manifest_paths:
        try:
            with mf.open("r", encoding="utf-8") as f:
                mdata = json.load(f)
        except Exception:
            continue

        sid = mdata.get("source_id") or mf.parent.name
        meta = source_metadata.get(sid, {})
        jur = meta.get("jurisdiction") or "CN"
        sample_urls = [s.get("public_url") for s in mdata.get("samples", []) if s.get("public_url")]

        routes = mdata.get("routes", [])
        for r_idx, r in enumerate(routes, start=1):
            if r.get("status") != 200:
                continue

            content_type = r.get("content_type", "")
            fix_name = r.get("fixture", "")
            if not fix_name:
                continue

            fix_file = mf.parent / fix_name
            if not fix_file.is_file():
                continue

            # Skip binary files
            if fix_file.suffix.lower() in BINARY_EXTENSIONS or any(
                b in content_type.lower()
                for b in ["pdf", "zip", "word", "excel", "image", "octet-stream"]
            ):
                continue

            try:
                raw_bytes = fix_file.read_bytes()
            except Exception:
                continue

            if any(raw_bytes.startswith(p) for p in BINARY_PREFIXES):
                continue

            enc = r.get("encoding", "utf-8")
            try:
                raw_payload = raw_bytes.decode(enc)
            except Exception:
                try:
                    raw_payload = raw_bytes.decode("utf-8", errors="replace")
                except Exception:
                    continue

            if not raw_payload.strip() or raw_payload.strip().endswith("…(truncated)"):
                continue
            if len(raw_payload) > max_char_length:
                continue

            p_hash = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()
            if (sid, p_hash) in seen_payload_hashes:
                continue
            seen_payload_hashes.add((sid, p_hash))

            # Determine MIME type
            if "json" in content_type:
                mime = "application/json"
            elif "xml" in content_type:
                mime = "application/xml"
            else:
                mime = "text/html"

            # Determine task_type and request_url
            r_path = r.get("path", "")
            base_url = sample_urls[0] if sample_urls else f"https://www.wlh-benchmark.test/{sid}"
            parsed_base = urlsplit(base_url)
            if r_path.startswith("http://") or r_path.startswith("https://"):
                req_url = r_path
            else:
                req_url = urlunsplit(
                    (
                        parsed_base.scheme,
                        parsed_base.netloc,
                        r_path if r_path.startswith("/") else f"/{r_path}",
                        "",
                        "",
                    )
                )

            is_sample_detail = any(
                (r_path and r_path in s) or (s and s.endswith(r_path)) for s in sample_urls
            )
            if is_sample_detail or (
                mime in ["application/json", "application/xml"] and not r_path.endswith("/")
            ):
                t_type = "content_page"
                t_id = f"content_{sid}_{r_idx:02d}"
            else:
                t_type = "list_page"
                t_id = f"list_{sid}_{r_idx:02d}"

            task = ProcessorBenchmarkTask(
                id=t_id,
                task_type=t_type,
                source_id=sid,
                jurisdiction=jur,
                request_url=req_url,
                mime_type=mime,
                raw_payload=raw_payload,
                payload_sha256=p_hash,
            )
            if t_type == "list_page":
                list_tasks_by_source.setdefault(sid, []).append(task)
            else:
                content_tasks_by_source.setdefault(sid, []).append(task)

    # 2. Ingest raw_candidates.jsonl (content pages)
    c_path = Path(candidates_path)
    if c_path.is_file():
        cand_items: list[dict[str, Any]] = []
        with c_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    cand_items.append(json.loads(line.strip()))

        has_trunc = any(
            str(it.get("raw_payload", "")).strip().endswith("…(truncated)") for it in cand_items
        )
        if has_trunc:
            try:
                from wlh.runbook_samples import hydrate_samples_from_store

                cached = load_cached_samples(DEFAULT_HTML_PATH)
                hydrated = hydrate_samples_from_store(cached, full=True, csv_path=Path(csv_path))
                by_sid_idx: dict[tuple[str, int], str] = {}
                for sid, s_list in hydrated.items():
                    for s_i, s_data in enumerate(s_list):
                        if s_data.get("raw_payload"):
                            by_sid_idx[(sid, s_i)] = s_data["raw_payload"]
                for it in cand_items:
                    cid = it.get("id", "")
                    for plan_idx, (p_sid, p_idx, _, _) in enumerate(CANDIDATE_PLAN, start=1):
                        if f"cand_{plan_idx:02d}" == cid:
                            if (p_sid, p_idx) in by_sid_idx:
                                it["raw_payload"] = by_sid_idx[(p_sid, p_idx)]
                            break
            except Exception:
                pass

        for it in cand_items:
            sid = it.get("source_id", "")
            raw_payload = str(it.get("raw_payload") or "")
            if not raw_payload.strip() or raw_payload.strip().endswith("…(truncated)"):
                continue
            if len(raw_payload) > max_char_length:
                continue

            raw_bytes = raw_payload[:64].encode("latin-1", errors="ignore")
            if any(raw_bytes.startswith(p) for p in BINARY_PREFIXES):
                continue

            p_hash = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()
            if (sid, p_hash) in seen_payload_hashes:
                continue
            seen_payload_hashes.add((sid, p_hash))

            # Determine mime
            if raw_payload.lstrip().startswith("{") or raw_payload.lstrip().startswith("["):
                mime = "application/json"
            elif raw_payload.lstrip().startswith("<?xml") or raw_payload.lstrip().startswith("<xml"):
                mime = "application/xml"
            else:
                mime = "text/html"

            task = ProcessorBenchmarkTask(
                id=f"content_{sid}_{it.get('id', 'sample')}",
                task_type="content_page",
                source_id=sid,
                jurisdiction=it.get("jurisdiction", "CN"),
                request_url=it.get("detail_url") or f"https://www.wlh-benchmark.test/{sid}",
                mime_type=mime,
                raw_payload=raw_payload,
                payload_sha256=p_hash,
            )
            content_tasks_by_source.setdefault(sid, []).append(task)

    # 3. Assemble final tasks capped at max_list_per_source and max_content_per_source
    all_sids = sorted(set(list_tasks_by_source.keys()) | set(content_tasks_by_source.keys()))
    tasks: list[ProcessorBenchmarkTask] = []
    for sid in all_sids:
        tasks.extend(list_tasks_by_source.get(sid, [])[:max_list_per_source])
        tasks.extend(content_tasks_by_source.get(sid, [])[:max_content_per_source])

    # Write output
    with out_file.open("w", encoding="utf-8") as f:
        for t in tasks:
            f.write(json.dumps(t.model_dump(), ensure_ascii=False) + "\n")

    return tasks


def run_preflight_checks(
    bundle_path: Path | str = DEFAULT_OUTPUT_DIR / "ground_truth_benchmark.jsonl",
) -> dict[str, Any]:
    """Execute pre-flight static verification checks before uploading to GPU worker."""
    b_path = Path(bundle_path)
    if not b_path.is_file():
        fallback = DEFAULT_OUTPUT_DIR / "annotation_pipeline.json"
        if b_path == DEFAULT_OUTPUT_DIR / "ground_truth_benchmark.jsonl" and fallback.is_file():
            b_path = fallback
        else:
            raise FileNotFoundError(f"Processor batch bundle not found: {b_path}")

    tasks: list[ProcessorBenchmarkTask] = []
    if b_path.suffix == ".json":
        raw_obj: Any = json.loads(b_path.read_text(encoding="utf-8"))
        items_list: list[object] = (
            cast(list[object], cast(dict[str, Any], raw_obj).get("items", []))
            if isinstance(raw_obj, dict)
            else cast(list[object], raw_obj)
        )
        for idx, item in enumerate(items_list, start=1):
            try:
                tasks.append(ProcessorBenchmarkTask.model_validate(item))
            except Exception as e:
                raise ValueError(f"Item {idx} violates ProcessorBenchmarkTask schema: {e}") from e
    else:
        with b_path.open("r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                if line.strip():
                    try:
                        tasks.append(ProcessorBenchmarkTask.model_validate_json(line.strip()))
                    except Exception as e:
                        raise ValueError(f"Line {line_num} violates ProcessorBenchmarkTask schema: {e}") from e

    if not tasks:
        raise ValueError("Batch bundle contains zero tasks.")

    list_tasks = [t for t in tasks if t.task_type == "list_page"]
    content_tasks = [t for t in tasks if t.task_type == "content_page"]

    if not list_tasks:
        raise ValueError("Batch bundle contains zero list_page tasks.")
    if not content_tasks:
        raise ValueError("Batch bundle contains zero content_page tasks.")

    BINARY_PREFIXES = (b"%PDF-", b"PK\x03\x04", b"\xd0\xcf\x11\xe0", b"\x89PNG")

    warnings: list[str] = []
    for t in tasks:
        # Check 1: No truncation
        if t.raw_payload.strip().endswith("…(truncated)"):
            raise ValueError(f"Task {t.id} has truncated payload (ends with …(truncated))")
        if re.search(r'<script\s+id=["\']wlh-', t.raw_payload):
            raise ValueError(f"Task {t.id} contains preview cache wrapper <script id=\"wlh-...")

        # Check 2: Pure text
        raw_b = t.raw_payload[:64].encode("latin-1", errors="ignore")
        if any(raw_b.startswith(p) for p in BINARY_PREFIXES):
            raise ValueError(f"Task {t.id} contains binary header")

        # Check 3: Token watermark check (< 100K token budget)
        estimated_tokens = len(t.raw_payload) // 4
        if estimated_tokens > 100_000:
            warnings.append(
                f"Task {t.id} ({t.source_id}) has estimated {estimated_tokens:,} tokens (> 100K token budget); "
                f"requires high-memory GPU context or chunked prefill exemption."
            )

    # Check 4: XGrammar JSON Schema validity
    from wlh.benchmark.schema import (
        get_content_page_json_schema,
        get_list_page_json_schema,
        validate_xgrammar_schema,
    )

    if not validate_xgrammar_schema(get_list_page_json_schema()):
        raise ValueError("ListPageGroundTruth JSON Schema is incompatible with XGrammar")
    if not validate_xgrammar_schema(get_content_page_json_schema()):
        raise ValueError("ContentPageGroundTruth JSON Schema is incompatible with XGrammar")

    # Check 5: Local Mock Dry-Run
    mock_list_task = list_tasks[0]
    mock_content_task = content_tasks[0]

    mock_list_output = ListPageGroundTruth(target_urls=[mock_list_task.request_url]).model_dump()
    mock_content_output = ContentPageGroundTruth(
        records=[
            RecordGroundTruth(
                doc_type="政策文件",
                markdown="# 标题\n\n正文内容。",
                title="测试标题",
                doc_number="第1号",
                published_at="2026-01-01",
                effective_at="2026-01-01",
                issuer="测试机关",
                attachment_urls=[],
            )
        ]
    ).model_dump()

    from wlh.benchmark.evaluator import evaluate_sample

    eval_list = evaluate_sample(
        prediction={"target_urls": [mock_list_task.request_url]},
        ground_truth=mock_list_output,
        sample_id=mock_list_task.id,
        source_id=mock_list_task.source_id,
        jurisdiction=mock_list_task.jurisdiction,
    )
    assert eval_list.overall_score >= 90.0, f"Mock list evaluation failed: {eval_list.overall_score}"

    eval_content = evaluate_sample(
        prediction=mock_content_output["records"][0],
        ground_truth=mock_content_output,
        sample_id=mock_content_task.id,
        source_id=mock_content_task.source_id,
        jurisdiction=mock_content_task.jurisdiction,
    )
    assert eval_content.overall_score >= 90.0, f"Mock content evaluation failed: {eval_content.overall_score}"

    return {
        "status": "passed",
        "total_tasks": len(tasks),
        "list_tasks": len(list_tasks),
        "content_tasks": len(content_tasks),
        "bundle_path": str(b_path),
        "warnings": warnings,
    }



def main(argv: Sequence[str] | None = None) -> None:
    """CLI entrypoint to extract representative benchmark candidate samples."""
    parser = argparse.ArgumentParser(
        description="Extract representative benchmark candidates from HTML verification cache."
    )
    parser.add_argument(
        "--html",
        type=Path,
        default=DEFAULT_HTML_PATH,
        help=f"Path to source-verification-samples.html (default: {DEFAULT_HTML_PATH})",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV_PATH,
        help=f"Path to source-adaptation-matrix.csv (default: {DEFAULT_CSV_PATH})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory to output benchmark dataset (default: {DEFAULT_OUTPUT_DIR})",
    )

    args = parser.parse_args(argv)

    print(f"Loading samples from {args.html} and metadata from {args.csv}...")
    candidates, manifest = extract_representative_candidates(
        html_path=args.html,
        csv_path=args.csv,
    )
    print(
        f"Extracted {len(candidates)} candidate items across "
        f"{manifest['metadata']['total_jurisdictions']} jurisdictions."
    )

    print(f"Writing outputs to {args.output_dir}...")
    write_benchmark_candidates(args.output_dir, candidates, manifest)

    jsonl_file = args.output_dir / "raw_candidates.jsonl"
    manifest_file = args.output_dir / "candidate_manifest.json"

    print(f"✓ Created {jsonl_file} ({jsonl_file.stat().st_size} bytes)")
    print(f"✓ Created {manifest_file} ({manifest_file.stat().st_size} bytes)")
    print("Candidate extraction successfully completed.")


if __name__ == "__main__":
    main()


