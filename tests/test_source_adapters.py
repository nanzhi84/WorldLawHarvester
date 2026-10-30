"""Production source configuration replay through collection, canonical projection and offline reparse."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import psycopg
import pytest
import yaml

from wlh.cleaners.delivery_quality import (
    assert_delivery_quality,
    verify_source_delivery,
)

from .bridge_fixture_support import (
    BridgeFixtureHandler as _BridgeFixtureHandler,
)
from .bridge_fixture_support import (
    bridge_fixture_servers as bridge_fixture_servers,
)


class TestAdaptedSourceConfigReplayAndZeroTargetGuard:
    @pytest.mark.parametrize(
        ("source_id", "expected_doc_type", "expected_substr"),
        [
            ("dd-017", "案例", "指导性案例220号"),
            ("dd-040", "政策文件", "2026年第1号"),
            ("dd-041", "案例", "第六十批指导性案例"),
            ("dd-102", "通知公告", "京汇罚〔2026〕15号"),
            ("dd-183", "政策文件", "地方政府专项债券用途调整操作指引"),
            ("dd-375", "行业标准", "国统字〔2019〕66号"),
            ("dd-448", "通知公告", "影剧备字〔2026〕第1230号"),
            ("dd-455", "通知公告", "2026年进口网络游戏审批信息"),
            ("dd-492", "法律法规", "105/2026/NĐ-CP"),
            ("dd-493", "法律法规", "48/2026/QĐ-TTg"),
            ("dd-501", "政策文件", "国办发〔2026〕12号"),
            ("dd-502", "法律法规", "国令第779号"),
            ("dd-513", "法律法规", "37/CT-TTg"),
            ("dd-495", "法律法规", "145/2026/TT-BTC"),
            ("dd-496", "案例", "1463/2026/DS-PT"),
            ("dd-499", "法律法规", "2026-08801"),
            ("dd-519", "案例", "選挙無効請求事件"),
            ("dd-520", "税务资料", "産科医療特別給付事業"),
            ("dd-527", "税务资料", "Tax Treatment of Research & Development Expenditure"),
            ("dd-548", "案例", "[2024] UKSC 1"),
            ("dd-549", "税务资料", "ADML3100"),
            ("dd-550", "法律法规", "GG"),
            ("dd-551", "案例", "VIII ZR 12/25"),
            ("dd-556", "案例", "4A_101/2026"),
            ("dd-558", "法律法规", "1"),
            ("dd-559", "法律法规", "1 CFR Part 1"),
            ("dd-560", "法律法规", "HB 1091"),
            ("dd-561", "案例", "25-1402"),
            ("dd-562", "案例", "26A388"),
            ("dd-563", "税务资料", "2026-16"),
            ("dd-582", "税务资料", "裁決"),
            ("dd-564", "法律法规", "A-1"),
            ("dd-565", "案例", "2022 SCC 18"),
            ("dd-570", "法律法规", "2024 No 1"),
            ("dd-572", "法律法规", "Act No. 718 of 1963"),
            ("dd-506", "案例", "HCAL 371/2021"),
            ("dd-507", "税务资料", "dipn03"),
            ("dd-512", "法律法规", "46/2026/QH16"),
            ("dd-514", "案例", "84/2026/AL"),
            ("dd-516", "税务资料", "Đề nghị hướng dẫn"),
            ("dd-517", "法律法规", "明治八年太政官布告第五十四号"),
            ("dd-575", "法律法规", "Luật sửa đổi, bổ sung một số điều của 09 luật về quân sự, quốc phòng"),
            ("dd-525", "法律法规", "Accountants Act 2004"),
            ("dd-557", "法律法规", "Public Law 118–5"),
            ("dd-571", "税务资料", "2026-NZSC-121"),
            ("dd-142", "其他", "「2026 정부혁신 왕중왕전」온라인 정책소통 OPEN!"),
            (
                "dd-019",
                "通知公告",
                "最高人民法院发布《关于审理涉及刑事犯罪的民事纠纷案件若干问题的规定》",
            ),
            ("dd-039", "政策文件", "检察机关深入开展“益路长征·赓续守护”专项监督"),
            ("dd-482", "通知公告", "关于在政府采购活动中对有关美国企业采取相关措施的通知"),
            ("dd-423", "通知公告", "宋志勇会见空客首席执行官"),
            ("dd-385", "通知公告", "关于开展季度调查监测的通知"),
            (
                "dd-265",
                "税务资料",
                "中国注册税务师协会关于第七届理事会部分理事及常务理事调整的公告",
            ),
            (
                "dd-153",
                "其他",
                "关于征集2026年度互联网域名管理技术国家工程实验室开放课题指南建议的通知",
            ),
            ("dd-108", "通知公告", "2025信息通信行业信用建设研讨会成功召开"),
            ("dd-198", "通知公告", "黑龙江省黑土资源交易“表土剥离独立交易窗口”上线"),
            ("dd-209", "通知公告", "广州市番禺城市排水管理有限公司"),
            ("dd-320", "通知公告", "山西省自然资源厅"),
            (
                "dd-323",
                "通知公告",
                "吉林省桦甸市半截沟区金多金属矿勘查探矿权挂牌出让成交结果公示",
            ),
            ("dd-324", "通知公告", "李强主持召开国务院常务会议"),
            ("dd-327", "通知公告", "《浙江省自然资源厅关于建立健全国土空间详细规划体系的通知》政策解读"),
            ("dd-337", "通知公告", "2026年第6期自贸港机关讲坛开讲"),
            ("dd-132", "其他", "商標規費收費標準"),
            ("dd-162", "财务报告", "外籍員工持股信託"),
            ("dd-509", "法律法规", "中央法規標準法"),
            ("dd-511", "税务资料", "所得稅法"),
            ("dd-577", "政策文件", "學生輔導法"),
            ("dd-578", "法律法规", "中華民國憲法"),
            ("dd-579", "法律法规", "動員戡亂完成憲政實施綱要"),
            ("dd-510", "案例", "113 年度台上字第 2290 號"),
            ("dd-580", "案例", "58175"),
            ("dd-143", "其他", "文献蓄積情報(特許・実用新案)"),
            ("dd-518", "政策文件", "政府調達"),
        ],
    )
    def test_adapted_production_source_configs_collect_and_reparse_against_local_samples(
        self,
        initialized_db: dict[str, str],
        bridge_fixture_servers: tuple[int, int],
        tmp_path: Path,
        source_id: str,
        expected_doc_type: str,
        expected_substr: str,
    ) -> None:
        """Production `sources/dd-*.yaml` extraction rules collect and offline-reparse cleanly against local fixtures."""
        import re
        from urllib.parse import urlsplit, urlunsplit

        port, port_v6 = bridge_fixture_servers
        _BridgeFixtureHandler.active_source_id = source_id

        try:
            repo_root = Path(__file__).resolve().parents[1]
            src_yaml_path = repo_root / "sources" / f"{source_id}.yaml"
            fixture_manifest_path = repo_root / "tests" / "fixtures" / source_id / "manifest.json"
            if not src_yaml_path.is_file() or not fixture_manifest_path.is_file():
                pytest.skip(f"Private source config or fixture for {source_id} not present")
            raw_cfg: dict[str, Any] = yaml.safe_load(
                src_yaml_path.read_text(encoding="utf-8")
            )
            sched: dict[str, Any] = raw_cfg["scheduler"]
            fetcher: dict[str, Any] = raw_cfg.setdefault("fetcher", {})
            parser: dict[str, Any] = raw_cfg.get("processor", {}).get("parser", {})

            orig_hosts = list(sched.get("allowed_hosts", []))
            primary_host = orig_hosts[0]
            secondary_hosts = orig_hosts[1:]

            rewritten_urls: list[str] = []
            entry_urls: list[str] = sched["entry_urls"]
            for entry_url in entry_urls:
                parts = urlsplit(entry_url)
                rewritten_urls.append(
                    urlunsplit(("http", f"127.0.0.1:{port}", parts.path, parts.query, ""))
                )
            sched["entry_urls"] = rewritten_urls
            sched["allowed_hosts"] = ["127.0.0.1", "::1"] if secondary_hosts else ["127.0.0.1"]
            if "host_path_scopes" in sched:
                orig_scopes: dict[str, list[str]] = sched["host_path_scopes"]
                new_scopes: dict[str, list[str]] = {}
                if primary_host in orig_scopes:
                    new_scopes["127.0.0.1"] = list(orig_scopes[primary_host])
                sec_scoped: list[str] = []
                for sec_host in secondary_hosts:
                    for p in orig_scopes.get(sec_host, []):
                        if p not in sec_scoped:
                            sec_scoped.append(p)
                if sec_scoped:
                    new_scopes["::1"] = sec_scoped
                sched["host_path_scopes"] = new_scopes
                allowed_prefixes: list[str] = list(sched.get("allowed_path_prefixes", []))
                for p_list in new_scopes.values():
                    for p in p_list:
                        if p not in allowed_prefixes:
                            allowed_prefixes.append(p)
                sched["allowed_path_prefixes"] = allowed_prefixes
            fetcher["delay"] = 0.0

            def _rewrite_pat(pat: str) -> str:
                out = pat
                for sec_host in secondary_hosts:
                    out = out.replace(re.escape(sec_host), rf"\[::1\]:{port_v6}")
                    out = out.replace(sec_host.replace(".", r"\."), rf"\[::1\]:{port_v6}")
                out = out.replace(re.escape(primary_host), rf"127\.0\.0\.1:{port}")
                out = out.replace(primary_host.replace(".", r"\."), rf"127\.0\.0\.1:{port}")
                out = re.sub(r"^\^https?://", "^http://", out)
                return out

            if "url_rewrites" in sched:
                for rw in sched["url_rewrites"]:
                    rw["pattern"] = _rewrite_pat(rw["pattern"])
                    rw["replacement"] = re.sub(r"^https://", "http://", rw["replacement"])

            if "documents" in parser and isinstance(parser["documents"], dict):
                docs = cast(dict[str, Any], parser["documents"])
                if "detail_url_patterns" in docs:
                    docs["detail_url_patterns"] = [
                        _rewrite_pat(str(pat)) for pat in docs["detail_url_patterns"]
                    ]
                if "exclude_detail_url_patterns" in docs:
                    docs["exclude_detail_url_patterns"] = [
                        _rewrite_pat(str(pat)) for pat in docs["exclude_detail_url_patterns"]
                    ]
                if "attachment_url_pattern" in docs:
                    docs["attachment_url_pattern"] = _rewrite_pat(
                        str(docs["attachment_url_pattern"])
                    )
                # Navigation patterns carry the same origin as document patterns.
                # Preserve their paths and matching rules when replaying locally.
                options: dict[str, Any] = parser.get("options", {})
                if "navigation_url_patterns" in options:
                    options["navigation_url_patterns"] = [
                        _rewrite_pat(str(pat)) for pat in options["navigation_url_patterns"]
                    ]

            replay_yaml = tmp_path / f"{source_id}-replay.yaml"
            replay_yaml.write_text(
                yaml.safe_dump(raw_cfg, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )

            from wlh.scheduler.runs import execute_run, prepare_run
            from wlh.storage.snapshots import project_canonical

            c_run = prepare_run(replay_yaml, "collect")
            c_res = execute_run(c_run)
            assert c_res["status"] == "complete", f"{source_id} collect failed: {c_res}"

            multi_resource_sources = {
                "dd-017",
                "dd-019",
                "dd-039",
                "dd-108",
                "dd-132",
                "dd-142",
                "dd-143",
                "dd-153",
                "dd-162",
                "dd-198",
                "dd-209",
                "dd-265",
                "dd-320",
                "dd-323",
                "dd-324",
                "dd-327",
                "dd-337",
                "dd-385",
                "dd-423",
                "dd-482",
                "dd-492",
                "dd-493",
                "dd-502",
                "dd-506",
                "dd-507",
                "dd-509",
                "dd-510",
                "dd-511",
                "dd-512",
                "dd-514",
                "dd-516",
                "dd-517",
                "dd-518",
                "dd-519",
                "dd-520",
                "dd-525",
                "dd-527",
                "dd-548",
                "dd-549",
                "dd-550",
                "dd-551",
                "dd-557",
                "dd-558",
                "dd-559",
                "dd-562",
                "dd-564",
                "dd-565",
                "dd-570",
                "dd-571",
                "dd-572",
                "dd-575",
                "dd-577",
                "dd-578",
                "dd-579",
                "dd-580",
            }

            with psycopg.connect(initialized_db["WLH_DATABASE_URL"]) as conn:
                from psycopg.rows import dict_row

                with conn.cursor(row_factory=dict_row) as cur:
                    rows = cur.execute(
                        """
                        SELECT f.source_id, r.id AS resource_id, d.*
                        FROM wlh.document_row d
                        JOIN wlh.version v ON v.id = d.version_id
                        JOIN wlh.resource r ON r.id = v.resource_id
                        JOIN wlh.frontier f ON f.id = r.frontier_id
                        WHERE f.source_id = %s
                        ORDER BY d.row_index, r.id
                        """,
                        (source_id,),
                    ).fetchall()
                    assert len(rows) >= 1, f"Expected document_row for {source_id}"
                    from wlh.storage.cas import Objects

                    objs = Objects()
                    quality_report = assert_delivery_quality(
                        rows,
                        objects=objs,
                        source_config=raw_cfg,
                        require_multi_resource=(source_id in multi_resource_sources),
                    )
                    assert quality_report["passed"] is True
                    assert quality_report["violations"] == []

                    verified = verify_source_delivery(
                        source_id,
                        require_multi_resource=(source_id in multi_resource_sources),
                    )
                    assert verified["passed"] is True

                    projected_rows = [project_canonical(r) for r in rows]
                    projected = next(
                        (
                            p
                            for p in projected_rows
                            if expected_substr in (p["doc_number"] or "")
                            or expected_substr in (p["title"] or "")
                        ),
                        projected_rows[0],
                    )
                    assert projected["doc_type"] == expected_doc_type
                    assert (
                        expected_substr in (projected["doc_number"] or "")
                        or expected_substr in (projected["title"] or "")
                    ), f"Expected {expected_substr} in {projected}"
                    if source_id in {
                        "dd-492",
                        "dd-493",
                        "dd-499",
                        "dd-507",
                        "dd-512",
                        "dd-513",
                        "dd-514",
                        "dd-560",
                        "dd-561",
                        "dd-562",
                        "dd-565",
                        "dd-575",
                    }:
                        assert {
                            r["resource_id"] for r in rows if r["original_hash"] is not None
                        } == {r["resource_id"] for r in rows}, (
                            f"Expected captured attachment/original for every resource of {source_id}"
                        )
                    if source_id in {"dd-512", "dd-516", "dd-565", "dd-575"}:
                        primary_rows = [r for r in rows if r["role"] == "primary"] or rows
                        for r in primary_rows:
                            md_text = objs.get(r["markdown_hash"]).decode(
                                "utf-8", errors="replace"
                            )
                            if source_id == "dd-575":
                                assert "Toàn văn hồ sơ dự án" in md_text
                            elif source_id == "dd-512":
                                assert (
                                    "Official Legal Document" in md_text or "Nghị quyết" in md_text
                                )
                            elif source_id == "dd-516":
                                assert r["doc_number"] is None
                                assert "Đề nghị hướng dẫn" in md_text
                                assert "Trả lời:" in md_text
                                ans_part = md_text.split("Trả lời:", 1)[1].strip()
                                assert "Căn cứ " in ans_part and len(ans_part) >= 50
                            elif source_id == "dd-565":
                                assert (
                                    md_text.count(
                                        "Full judgment reasons of the Supreme Court of Canada"
                                    )
                                    == 1
                                )
                    if source_id in {"dd-550", "dd-558"}:
                        assert rows[0]["original_media_type"] == "application/zip"
                        assert (projected["original_file"] or "").endswith(".zip")
                    if source_id == "dd-512":
                        assert projected["issuer"] == "Quốc hội"
                        assert projected["published_at"] == "2026-08-24"
                        assert projected["title"] != projected["doc_number"]
                        assert not re.fullmatch(
                            r"(?:Luật|Pháp lệnh|Nghị quyết)(?:\s+số)?\s+"
                            + re.escape(projected["doc_number"] or ""),
                            (projected["title"] or "").strip(),
                            re.I,
                        ), (
                            f"dd-512 title {projected['title']!r} must include descriptive subject (Trích yếu), "
                            "not just bare document number"
                        )
                    if source_id == "dd-516":
                        assert projected["issuer"] == "Bộ Tài chính"
                        assert not (projected["title"] or "").startswith("Bộ Tài")
                        assert not (projected["title"] or "").endswith("...")
                    if source_id == "dd-575":
                        assert "Bộ Quốc phòng" in (projected["issuer"] or "")
                        for r in rows:
                            assert r["doc_number"] is None
                            assert r["role"] == "primary"
                            assert r["original_hash"] is not None
                    if source_id == "dd-548":
                        assert projected["issuer"] == "The Supreme Court"
                        assert projected["published_at"] == "2024-01-11"
                    if source_id == "dd-496":
                        assert projected["issuer"] == "TAND TP. Cần Thơ"
                        assert projected["published_at"] == "2026-10-15"
                    if source_id == "dd-502":
                        assert projected["issuer"] == "国务院"
                        assert projected["published_at"] == "2026-10-03"
                    if source_id == "dd-520":
                        reply = next(p for p in projected_rows if p["title"] == "産科医療特別給付事業に基づき支払われる給付金の所得税法上の取扱いについて")
                        assert reply["issuer"] == "国税庁課税部審理室長"
                    if source_id == "dd-549":
                        assert projected["issuer"] == "HM Revenue & Customs"
                        assert projected["published_at"] == "2016-03-11"
                    if source_id == "dd-562":
                        assert projected["issuer"] == "Supreme Court of the United States"
                        assert projected["published_at"] in {"2026-09-25", "2026-06-30"}
                        assert " v. " in (projected["title"] or "")
                    if source_id == "dd-578":
                        const_row = next(p for p in projected_rows if p["title"] == "中華民國憲法")
                        assert const_row["published_at"] == "1947-01-01"
                        assert const_row["effective_at"] == "1947-12-25"
                        assert const_row["status"] == "現行有效"
                        assert all("＞" not in (p["issuer"] or "") and ">" not in (p["issuer"] or "") for p in projected_rows)
                    if source_id == "dd-579":
                        assert projected["effective_at"] == "1947-07-19"
                        assert projected["expired_at"] == "1991-05-17"
                        assert projected["status"] == "已廢止"
                        assert projected["issuer"] == "行政院"
                        assert all("＞" not in (p["issuer"] or "") and ">" not in (p["issuer"] or "") for p in projected_rows)
                    if source_id == "dd-509":
                        assert projected["published_at"] == "2004-05-19"
                        assert projected["effective_at"] == "2004-05-19"
                        from wlh.processor.parsers.html.fields import extract_detail_fields

                        issuer_rule = raw_cfg["processor"]["parser"]["documents"]["fields"]["issuer"]
                        treaty_fields, _, _ = extract_detail_fields(
                            {"issuer": issuer_rule},
                            '<table class="table"><tr><th>簽約國：</th><td>歐洲地區 ＞ 波蘭</td></tr></table>'.encode(
                                "utf-8"
                            ),
                            "html",
                            {"charset": "utf-8"},
                            "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=Y0010530",
                        )
                        assert treaty_fields.get("issuer") != "歐洲地區 ＞ 波蘭"

                        domestic_fields, _, _ = extract_detail_fields(
                            {"issuer": issuer_rule},
                            '<table class="table"><tr><th>法規類別：</th><td>行政 ＞ 臺灣省政府 ＞ 組織目</td></tr></table>'.encode(
                                "utf-8"
                            ),
                            "html",
                            {"charset": "utf-8"},
                            "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=A0010001",
                        )
                        assert domestic_fields.get("issuer") == "臺灣省政府"
                    if source_id == "dd-162":
                        from wlh.processor.parsers.html.fields import extract_detail_fields

                        issuer_rule = raw_cfg["processor"]["parser"]["documents"]["fields"]["issuer"]
                        law_fields, _, _ = extract_detail_fields(
                            {"issuer": issuer_rule},
                            '<div id="lawText"><span id="lblUnit">市場監理 &gt; 證券商管理</span></div>'.encode(
                                "utf-8"
                            ),
                            "html",
                            {"charset": "utf-8"},
                            "https://twse-regulation.twse.com.tw/TW/lawsystem/DAT0201.aspx?FLCODE=FL007009",
                        )
                        assert law_fields.get("issuer") == "臺灣證券交易所股份有限公司"

            rp_run = prepare_run(replay_yaml, "reparse")
            rp_res = execute_run(rp_run)
            assert rp_res["status"] == "complete", f"{source_id} reparse failed: {rp_res}"
            assert rp_res["report"]["external_requests"] == 0
        finally:
            _BridgeFixtureHandler.active_source_id = ""
