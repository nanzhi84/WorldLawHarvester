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
    bridge_fixture_servers as bridge_fixture_servers,
)


class TestAdaptedSourceConfigReplayAndZeroTargetGuard:
    @pytest.mark.parametrize(
        ("source_id", "expected_doc_type", "expected_substr"),
        [
            ("dd-017", "案例", "指导性案例271号"),
            ("dd-040", "政策文件", "2026年第2号"),
            ("dd-041", "案例", "第六十批指导性案例"),
            ("dd-102", "通知公告", "京汇罚〔2026〕27号"),
            ("dd-183", "政策文件", "推进政府债务信息公开 筑牢风险防控底线——财政部印发《地方政府债务信息公开办法》"),
            ("dd-375", "行业标准", "国统字〔2019〕66号"),
            ("dd-448", "通知公告", "影剧备字〔2026〕第1133号"),
            ("dd-455", "通知公告", "2026年进口网络游戏审批信息"),
            ("dd-492", "法律法规", "10744/VPCP-CN"),
            ("dd-493", "法律法规", "113/2026/VBHN-TT-NHNN"),
            ("dd-501", "政策文件", "中华人民共和国国家发展和改革委员会令（第45号）"),
            ("dd-502", "法律法规", "河南省人民政府令第128号"),
            ("dd-513", "法律法规", "227-QĐ/TW"),
            ("dd-495", "法律法规", "2030/QĐ-TTg"),
            ("dd-496", "案例", "39/2026/QĐST-HNGĐ"),
            ("dd-499", "法律法规", "2026-21699"),
            ("dd-519", "案例", "選挙無効請求事件"),
            ("dd-520", "税务资料", "産科医療特別給付事業"),
            ("dd-527", "税务资料", "Obtaining a Copy of Documents Issued by IRAS"),
            ("dd-548", "案例", "[2026] UKFTT 01453 (GRC)"),
            ("dd-549", "税务资料", "ARTG2100"),
            ("dd-550", "法律法规", "EheRG 1"),
            ("dd-551", "案例", "2 ARs 569/09"),
            ("dd-556", "案例", "5A_959/2026"),
            ("dd-558", "法律法规", "1"),
            ("dd-559", "法律法规", "1 CFR Part 9"),
            ("dd-560", "法律法规", "HB 1185"),
            ("dd-561", "案例", "24-1921"),
            ("dd-562", "案例", "23A843"),
            ("dd-563", "税务资料", "2026-40"),
            ("dd-582", "税务资料", "裁決"),
            ("dd-564", "法律法规", "C-49"),
            ("dd-565", "案例", "2026 SCC 30"),
            ("dd-570", "法律法规", "2026 No 74"),
            ("dd-572", "法律法规", "Western Cape Provincial Gazette 9196 on 18 February 2026 (372.7 KB)"),
            ("dd-506", "案例", "CACV 382/2024"),
            ("dd-507", "税务资料", "dipn03"),
            ("dd-512", "法律法规", "35/2026/QH16"),
            ("dd-514", "案例", "81/2025/AL"),
            ("dd-516", "税务资料", "Công ty chúng tôi có dự kiến điều chỉnh tiến độ thực hiện dự án"),
            ("dd-517", "法律法规", "明治八年太政官布告第五十四号"),
            ("dd-575", "法律法规", "Luật sửa đổi, bổ sung Điều 6 và Phụ lục IV về"),
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

        if True:
            repo_root = Path(__file__).resolve().parents[1]
            src_yaml_path = repo_root / "sources" / f"{source_id}.yaml"
            fixture_manifest_path = repo_root / "tests" / "fixtures" / source_id / "manifest.json"
            assert src_yaml_path.is_file(), f"Missing source config {src_yaml_path}"
            assert fixture_manifest_path.is_file(), f"Missing fixture manifest {fixture_manifest_path}"

            from wlh.fixtures import load_fixture_manifest
            from wlh.runbook_samples import STRATA_BADGE_LABELS, stratify_verification_samples

            manifest = load_fixture_manifest(repo_root / "tests" / "fixtures", source_id)
            manifest_strata_raw = manifest.get("sampling_strata")
            assert isinstance(manifest_strata_raw, list)
            manifest_strata = [str(s) for s in cast(list[Any], manifest_strata_raw)]
            assert len(manifest_strata) >= 1, (
                f"{source_id} manifest.json must declare non-empty sampling_strata"
            )
            assert set(manifest_strata).issubset(set(STRATA_BADGE_LABELS.keys())), (
                f"{source_id} manifest.json has invalid sampling_strata: {manifest_strata}"
            )
            expectations = cast(dict[str, Any], manifest.get("expectations") or {})
            assert expectations.get("doc_type") == expected_doc_type
            assert expectations.get("substr") == expected_substr
            require_multi = bool(
                expectations.get("require_multi_resource", source_id != "dd-375")
            )
            assert require_multi == (source_id != "dd-375")

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
                        require_multi_resource=require_multi,
                    )
                    assert quality_report["passed"] is True
                    assert quality_report["violations"] == []

                    verified = verify_source_delivery(
                        source_id,
                        require_multi_resource=require_multi,
                    )
                    assert verified["passed"] is True

                    # Verify Anomaly-First Stratified Sampling (`sampling_strata`) on replayed resources
                    primary_by_res: dict[str, dict[str, Any]] = {}
                    for r in rows:
                        rid = str(r["resource_id"])
                        if rid not in primary_by_res or r["role"] != "independent_attachment":
                            if rid not in primary_by_res:
                                primary_by_res[rid] = dict(r)
                    strat_candidates: list[dict[str, Any]] = []
                    for rid, pr in primary_by_res.items():
                        md_val = objs.get(pr["markdown_hash"]).decode("utf-8", errors="replace")
                        strat_candidates.append(
                            {
                                "resource_id": rid,
                                "title": pr.get("title"),
                                "doc_number": pr.get("doc_number"),
                                "effective_at": pr.get("effective_at"),
                                "markdown": md_val,
                            }
                        )
                    stratified_replay = stratify_verification_samples(
                        strat_candidates, max_items=len(strat_candidates)
                    )
                    assert len(stratified_replay) == len(strat_candidates)
                    observed_strata = {
                        tag
                        for item in stratified_replay
                        for tag in item.get("sampling_strata", [])
                    }
                    assert observed_strata.issubset(set(STRATA_BADGE_LABELS.keys()))
                    assert "p5_shortest" in observed_strata
                    if require_multi:
                        assert "p95_longest" in observed_strata

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

                    if expectations.get("require_original_for_all_resources"):
                        assert {
                            r["resource_id"] for r in rows if r["original_hash"] is not None
                        } == {r["resource_id"] for r in rows}, (
                            f"Expected captured attachment/original for every resource of {source_id}"
                        )

                    primary_rows = [r for r in rows if r["role"] == "primary"] or rows
                    if "primary_markdown_contains_any" in expectations:
                        any_tokens = [str(t) for t in expectations["primary_markdown_contains_any"]]
                        for r in primary_rows:
                            md_text = objs.get(r["markdown_hash"]).decode("utf-8", errors="replace")
                            assert any(tok in md_text for tok in any_tokens), (
                                f"Expected one of {any_tokens} in {source_id} markdown"
                            )
                    if "primary_markdown_contains_all" in expectations:
                        all_tokens = [str(t) for t in expectations["primary_markdown_contains_all"]]
                        for r in primary_rows:
                            md_text = objs.get(r["markdown_hash"]).decode("utf-8", errors="replace")
                            for tok in all_tokens:
                                assert tok in md_text, f"Expected {tok!r} in {source_id} markdown"
                            if "Trả lời:" in all_tokens:
                                ans_part = md_text.split("Trả lời:", 1)[1].strip()
                                assert len(ans_part) >= 50
                    if "primary_markdown_exact_occurrences" in expectations:
                        occ_map = cast(
                            dict[str, int], expectations["primary_markdown_exact_occurrences"]
                        )
                        for r in primary_rows:
                            md_text = objs.get(r["markdown_hash"]).decode("utf-8", errors="replace")
                            for phrase, exp_cnt in occ_map.items():
                                assert md_text.count(phrase) == exp_cnt

                    if expectations.get("all_rows_doc_number_is_null"):
                        for r in rows:
                            assert r["doc_number"] is None
                    if "all_rows_role" in expectations:
                        for r in rows:
                            assert r["role"] == expectations["all_rows_role"]
                    if "original_media_type" in expectations:
                        assert rows[0]["original_media_type"] == expectations["original_media_type"]
                    if "original_file_suffix" in expectations:
                        assert (projected["original_file"] or "").endswith(
                            str(expectations["original_file_suffix"])
                        )
                    if "issuer" in expectations:
                        assert projected["issuer"] == expectations["issuer"]
                    if "issuer_contains" in expectations:
                        assert str(expectations["issuer_contains"]) in (projected["issuer"] or "")
                    if "issuer_forbidden_chars" in expectations:
                        for ch in expectations["issuer_forbidden_chars"]:
                            assert all(str(ch) not in (p["issuer"] or "") for p in projected_rows)
                    if "published_at" in expectations:
                        assert projected["published_at"] == expectations["published_at"]
                    if "published_at_in" in expectations:
                        assert projected["published_at"] in set(expectations["published_at_in"])
                    if "effective_at" in expectations:
                        assert projected["effective_at"] == expectations["effective_at"]
                    if "expired_at" in expectations:
                        assert projected["expired_at"] == expectations["expired_at"]
                    if "status" in expectations:
                        assert projected["status"] == expectations["status"]
                    if "title_contains" in expectations:
                        assert str(expectations["title_contains"]) in (projected["title"] or "")
                    if expectations.get("title_not_equal_doc_number"):
                        assert projected["title"] != projected["doc_number"]
                    if "title_not_bare_doc_number_prefix_regex" in expectations:
                        pref_re = str(expectations["title_not_bare_doc_number_prefix_regex"])
                        assert not re.fullmatch(
                            pref_re + re.escape(projected["doc_number"] or ""),
                            (projected["title"] or "").strip(),
                            re.I,
                        )
                    if "title_not_startswith" in expectations:
                        assert not (projected["title"] or "").startswith(
                            str(expectations["title_not_startswith"])
                        )
                    if "title_not_endswith" in expectations:
                        assert not (projected["title"] or "").endswith(
                            str(expectations["title_not_endswith"])
                        )
                    for row_spec in cast(
                        list[dict[str, Any]], expectations.get("specific_row_assertions") or []
                    ):
                        target_row = next(
                            p for p in projected_rows if p["title"] == row_spec["match_title"]
                        )
                        for k, v in row_spec.items():
                            if k != "match_title":
                                assert target_row[k] == v

                    for probe in cast(
                        list[dict[str, Any]], expectations.get("field_rule_probes") or []
                    ):
                        from wlh.processor.parsers.html.fields import extract_detail_fields

                        field_name = str(probe["field"])
                        rule_def = raw_cfg["processor"]["parser"]["documents"]["fields"][field_name]
                        extracted_fields, _, _ = extract_detail_fields(
                            {field_name: rule_def},
                            str(probe["html"]).encode("utf-8"),
                            "html",
                            {"charset": "utf-8"},
                            str(probe["url"]),
                        )
                        if "expected_equal" in probe:
                            assert extracted_fields.get(field_name) == probe["expected_equal"]
                        if "expected_not_equal" in probe:
                            assert extracted_fields.get(field_name) != probe["expected_not_equal"]

            rp_run = prepare_run(replay_yaml, "reparse")
            rp_res = execute_run(rp_run)
            assert rp_res["status"] == "complete", f"{source_id} reparse failed: {rp_res}"
            assert rp_res["report"]["external_requests"] == 0
