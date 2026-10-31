"""Externally visible delivery metadata and text quality contracts."""

from __future__ import annotations

from typing import Any

import pytest

from wlh.cleaners.delivery_quality import (
    assert_delivery_quality,
    audit_delivery_rows,
)
from wlh.core import WlhError

from .bridge_fixture_support import (
    bridge_fixture_servers as bridge_fixture_servers,
)


class TestDeliverySemanticQualityContract:
    def test_valid_multi_resource_delivery_rows_pass_semantic_quality_audit(
        self, wlh_env: dict[str, str]
    ) -> None:
        """Multi-resource delivery rows with distinct substantive titles, valid metadata, and clean CAS Markdown pass semantic quality audit."""
        from wlh.storage.cas import Objects

        objs = Objects()
        md1_hash = objs.put(
            (
                "# 中华人民共和国数据安全法\n\n"
                "第一条 为了规范数据处理活动，保障数据安全，促进数据开发利用，"
                "保护个人、组织的合法权益，维护国家主权、安全和发展利益，制定本法。"
            ).encode("utf-8")
        )
        md2_hash = objs.put(
            (
                "# 中华人民共和国个人信息保护法\n\n"
                "第一条 为了保护个人信息权益，规范个人信息处理活动，促进个人信息合理利用，"
                "根据宪法，制定本法。"
            ).encode("utf-8")
        )
        rows = [
            {
                "source_id": "dd-901",
                "resource_id": "res-001",
                "row_index": 1,
                "role": "primary",
                "title": "中华人民共和国数据安全法",
                "markdown_file": f"数据安全法_{'a' * 8}_{md1_hash[:8]}.md",
                "doc_type": "法律法规",
                "acquired_at": "2026-10-25 06:00:00",
                "original_file": None,
                "volume_order": None,
                "doc_number": "主席令第八十四号",
                "issuer": "全国人民代表大会常务委员会",
                "published_at": "2021-06-10",
                "effective_at": "2021-09-01",
                "expired_at": None,
                "status": "有效",
                "source_name": "国家法律法规数据库",
                "detail_url": "https://flk.npc.gov.cn/detail1.html",
                "source_record_hash": "a" * 64,
                "markdown_hash": md1_hash,
                "original_hash": None,
            },
            {
                "source_id": "dd-901",
                "resource_id": "res-002",
                "row_index": 1,
                "role": "primary",
                "title": "中华人民共和国个人信息保护法",
                "markdown_file": f"个人信息保护法_{'b' * 8}_{md2_hash[:8]}.md",
                "doc_type": "法律法规",
                "acquired_at": "2026-10-25 06:01:00",
                "original_file": None,
                "volume_order": None,
                "doc_number": "主席令第九十一号",
                "issuer": "全国人民代表大会常务委员会",
                "published_at": "2021-08-20",
                "effective_at": "2021-11-01",
                "expired_at": None,
                "status": "有效",
                "source_name": "国家法律法规数据库",
                "detail_url": "https://flk.npc.gov.cn/detail2.html",
                "source_record_hash": "b" * 64,
                "markdown_hash": md2_hash,
                "original_hash": None,
            },
        ]
        report = audit_delivery_rows(rows, objects=objs, require_multi_resource=True)
        assert report["passed"] is True
        assert report["violations"] == []

    def test_defect_rows_report_boilerplate_duplicate_title_polluted_doc_number_and_signature_stub_violations(
        self, wlh_env: dict[str, str]
    ) -> None:
        """Semantic quality audit deterministically flags boilerplate titles, duplicate titles, polluted doc_numbers, signature-only stubs, duplicated body paragraphs, and single-resource collections."""
        from wlh.storage.cas import Objects

        objs = Objects()
        sig_stub_hash = objs.put(
            b"Signer: Ministry Office\nSigned at: 2026-10-23\nCertificate ID: 8849-1102"
        )
        repeated_para = (
            "Pursuant to Section 42 of the Administrative Procedure Act, the agency hereby "
            "establishes mandatory compliance reporting standards for all regulated entities."
        )
        dup_body_hash = objs.put(
            f"# Administrative Compliance Rule\n\n{repeated_para}\n\n{repeated_para}".encode(
                "utf-8"
            )
        )
        clean_md_hash = objs.put(
            (
                "# Substantive Legal Provision\n\n"
                "Article 1. All administrative agencies shall publish normative decisions in the official gazette."
            ).encode("utf-8")
        )

        # 1. Single resource in collection + boilerplate title + signature-only markdown + polluted doc_number
        single_defect_rows = [
            {
                "source_id": "dd-455",
                "resource_id": "res-only-1",
                "row_index": 1,
                "role": "primary",
                "title": "国家新闻出版署",
                "markdown_file": f"国家新闻出版署_{'c' * 8}_{sig_stub_hash[:8]}.md",
                "doc_type": "通知公告",
                "acquired_at": "2026-10-25 06:00:00",
                "original_file": None,
                "volume_order": None,
                "doc_number": "国新出发〔2026〕1号\n附表说明及长篇正文污染内容",
                "issuer": "国家新闻出版署",
                "published_at": "2026-01-26",
                "effective_at": None,
                "expired_at": None,
                "status": None,
                "source_name": "国家新闻出版署",
                "detail_url": "https://www.nppa.gov.cn/detail1.html",
                "source_record_hash": "c" * 64,
                "markdown_hash": sig_stub_hash,
                "original_hash": None,
            }
        ]
        report_single = audit_delivery_rows(
            single_defect_rows, objects=objs, require_multi_resource=True
        )
        assert report_single["passed"] is False
        codes_single = set(
            report_single.get("violation_codes")
            or [v["code"] for v in report_single["violations"]]
        )
        assert "single_resource_in_collection" in codes_single
        assert "boilerplate_or_garbage_title" in codes_single
        assert "polluted_or_malformed_doc_number" in codes_single
        assert "signature_only_markdown" in codes_single

        # 2. Duplicate resource titles + boilerplate attachment title ("Tải file đính kèm") + duplicated body markdown + long-title-duplicating doc_number
        long_shared_title = (
            "Administrative Enforcement Guidelines on Cross-Border Financial Data Transfers"
        )
        multi_defect_rows = [
            {
                "source_id": "dd-902",
                "resource_id": "res-dup-1",
                "row_index": 1,
                "role": "primary",
                "title": long_shared_title,
                "markdown_file": f"guidelines1_{'d' * 8}_{dup_body_hash[:8]}.md",
                "doc_type": "政策文件",
                "acquired_at": "2026-10-25 06:00:00",
                "original_file": None,
                "volume_order": None,
                "doc_number": long_shared_title,
                "issuer": "Financial Regulatory Authority",
                "published_at": "2026-05-01",
                "effective_at": None,
                "expired_at": None,
                "status": None,
                "source_name": "Financial Regulatory Portal",
                "detail_url": "https://reg.example.org/doc1",
                "source_record_hash": "d" * 64,
                "markdown_hash": dup_body_hash,
                "original_hash": None,
            },
            {
                "source_id": "dd-902",
                "resource_id": "res-dup-2",
                "row_index": 1,
                "role": "primary",
                "title": long_shared_title,
                "markdown_file": f"guidelines2_{'e' * 8}_{clean_md_hash[:8]}.md",
                "doc_type": "政策文件",
                "acquired_at": "2026-10-25 06:01:00",
                "original_file": None,
                "volume_order": None,
                "doc_number": None,
                "issuer": "Financial Regulatory Authority",
                "published_at": "2026-05-02",
                "effective_at": None,
                "expired_at": None,
                "status": None,
                "source_name": "Financial Regulatory Portal",
                "detail_url": "https://reg.example.org/doc2",
                "source_record_hash": "e" * 64,
                "markdown_hash": clean_md_hash,
                "original_hash": None,
            },
            {
                "source_id": "dd-902",
                "resource_id": "res-dup-2",
                "row_index": 2,
                "role": "independent_attachment",
                "title": "Tải file đính kèm",
                "markdown_file": f"attach_{'e' * 8}_{clean_md_hash[:8]}.md",
                "doc_type": "政策文件",
                "acquired_at": "2026-10-25 06:01:00",
                "original_file": None,
                "volume_order": None,
                "doc_number": None,
                "issuer": None,
                "published_at": None,
                "effective_at": None,
                "expired_at": None,
                "status": None,
                "source_name": "Financial Regulatory Portal",
                "detail_url": "https://reg.example.org/doc2",
                "source_record_hash": "e" * 64,
                "markdown_hash": clean_md_hash,
                "original_hash": None,
            },
        ]
        report_multi = audit_delivery_rows(
            multi_defect_rows, objects=objs, require_multi_resource=True
        )
        assert report_multi["passed"] is False
        codes_multi = set(
            report_multi.get("violation_codes")
            or [v["code"] for v in report_multi["violations"]]
        )
        assert "duplicate_resource_titles" in codes_multi
        assert "boilerplate_or_garbage_title" in codes_multi
        assert "polluted_or_malformed_doc_number" in codes_multi
        assert "duplicated_body_markdown" in codes_multi

        for bad_title in ("Tải file đính kèm", "Contents", "附件下载"):
            bad_title_row = {
                **multi_defect_rows[1],
                "title": bad_title,
                "doc_number": "DOC-2026-" + ("X" * 130),
            }
            rep = audit_delivery_rows([bad_title_row], objects=objs, require_multi_resource=False)
            rep_codes = set(
                rep.get("violation_codes") or [v["code"] for v in rep["violations"]]
            )
            assert "boilerplate_or_garbage_title" in rep_codes
            assert "polluted_or_malformed_doc_number" in rep_codes

        with pytest.raises(WlhError) as exc:
            assert_delivery_quality(
                multi_defect_rows, objects=objs, require_multi_resource=True
            )
        assert exc.value.code == "delivery_quality_failed"

    def test_vietnamese_paywall_placeholder_and_stub_attachment_rows_fail_semantic_quality_audit(
        self, wlh_env: dict[str, str]
    ) -> None:
        """Vietnamese login-wall/un-uploaded placeholders, gazette running-header attachment stubs, and UUID/salutation-polluted titles fail semantic quality audit."""
        from wlh.storage.cas import Objects

        objs = Objects()
        clean_md_hash = objs.put(
            (
                "# Luật Đầu tư công 2026\n\n"
                "Điều 1. Phạm vi điều chỉnh. Luật này quy định việc quản lý và sử dụng vốn đầu tư công; "
                "quản lý nhà nước về đầu tư công; quyền, nghĩa vụ và trách nhiệm của cơ quan, đơn vị, "
                "tổ chức, cá nhân có liên quan đến hoạt động đầu tư công."
            ).encode("utf-8")
        )
        base_valid_row: dict[str, Any] = {
            "source_id": "dd-495",
            "resource_id": "res-vn-1",
            "row_index": 1,
            "role": "primary",
            "title": "Luật Đầu tư công 2026",
            "markdown_file": f"luat_dau_tu_cong_{'a' * 8}_{clean_md_hash[:8]}.md",
            "doc_type": "法律法规",
            "acquired_at": "2026-10-25 06:00:00",
            "original_file": None,
            "volume_order": None,
            "doc_number": "58/2026/QH15",
            "issuer": "Quốc hội",
            "published_at": "2026-06-15",
            "effective_at": "2026-09-01",
            "expired_at": None,
            "status": "Còn hiệu lực",
            "source_name": "LuatVietnam 法规库",
            "detail_url": "https://luatvietnam.vn/dau-tu/luat-dau-tu-cong-2026-123456-d1.html",
            "source_record_hash": "a" * 64,
            "markdown_hash": clean_md_hash,
            "original_hash": None,
        }

        # 1. Primary rows containing Vietnamese VIP/login-wall or un-uploaded placeholders padded with metadata
        placeholder_samples = (
            (
                "# Thông tư 145/2026/TT-BTC\n\n"
                "Văn bản này đang cập nhật nội dung. Mời quý khách xem nội dung văn bản dưới dạng file PDF... "
                "Bạn chưa Đăng nhập thành viên. Vui lòng Đăng nhập để tải văn bản.\n\n"
                "| Thuộc tính | Giá trị |\n| --- | --- |\n| Số hiệu | 145/2026/TT-BTC |\n| Cơ quan ban hành | Bộ Tài chính |"
            ),
            (
                "# Quyết định 88/2026/QĐ-TTg\n\n"
                "Bạn chưa Đăng nhập thành viên. Đây là tiện ích dành cho tài khoản Tiêu chuẩn hoặc Nâng cao 6 tháng trở lên. "
                "Vui lòng Đăng nhập để xem chi tiết Nội dung.\n\n"
                "| Thuộc tính | Giá trị |\n| --- | --- |\n| Số hiệu | 88/2026/QĐ-TTg |"
            ),
            (
                "# Nghị quyết 46/2026/QH16\n\n"
                "Vui lòng xem nội dung chi tiết trong file đính kèm."
            ),
        )
        for sample_md in placeholder_samples:
            sample_hash = objs.put(sample_md.encode("utf-8"))
            placeholder_row = {
                **base_valid_row,
                "markdown_file": f"placeholder_{'b' * 8}_{sample_hash[:8]}.md",
                "markdown_hash": sample_hash,
            }
            rep_placeholder = audit_delivery_rows(
                [placeholder_row], objects=objs, require_multi_resource=False
            )
            assert rep_placeholder["passed"] is False
            codes_placeholder = set(
                rep_placeholder.get("violation_codes")
                or [v["code"] for v in rep_placeholder["violations"]]
            )
            assert codes_placeholder & {
                "blocked_or_placeholder_markdown",
                "stub_markdown",
            }, (
                f"Expected blocked_or_placeholder_markdown or stub_markdown for {sample_md!r}, "
                f"got {codes_placeholder}"
            )

        # 2. independent_attachment row whose Markdown is only a gazette running header stub
        gazette_stub_md = "CÔNG BÁO/Số 560/Ngày 23-10-2026\n\nCÔNG BÁO/Số 560/Ngày 23-10-2026"
        gazette_stub_hash = objs.put(gazette_stub_md.encode("utf-8"))
        stub_att_row = {
            **base_valid_row,
            "source_id": "dd-493",
            "row_index": 2,
            "role": "independent_attachment",
            "title": "Quyết định 48/2026/QĐ-TTg (Bản PDF Công báo)",
            "markdown_file": f"congbao_stub_{'c' * 8}_{gazette_stub_hash[:8]}.md",
            "original_file": f"congbao_stub_{'c' * 8}_{gazette_stub_hash[:8]}.pdf",
            "markdown_hash": gazette_stub_hash,
            "original_hash": gazette_stub_hash,
        }
        rep_att = audit_delivery_rows(
            [stub_att_row], objects=objs, require_multi_resource=False
        )
        assert rep_att["passed"] is False
        codes_att = set(
            rep_att.get("violation_codes") or [v["code"] for v in rep_att["violations"]]
        )
        assert "stub_markdown" in codes_att

        # 3. Rows whose title ends with a raw 32-hex UUID filename suffix or starts with an unstripped salutation fragment
        for polluted_title in (
            "Luật Phát triển đô thị - 690779d6257c4649a72bd5ce76e64cb1.pdf",
            "Bộ Tài Chính. Công ty tôi hoạt động trong lĩnh vực...",
        ):
            bad_title_row = {
                **base_valid_row,
                "title": polluted_title,
            }
            rep_title = audit_delivery_rows(
                [bad_title_row], objects=objs, require_multi_resource=False
            )
            assert rep_title["passed"] is False
            codes_title = set(
                rep_title.get("violation_codes")
                or [v["code"] for v in rep_title["violations"]]
            )
            assert "boilerplate_or_garbage_title" in codes_title, (
                f"Expected boilerplate_or_garbage_title for {polluted_title!r}, got {codes_title}"
            )

    def test_multi_agency_joint_order_semicolon_doc_number_passes_while_long_polluted_doc_number_fails(
        self, wlh_env: dict[str, str]
    ) -> None:
        """Taiwan multi-ministry joint order doc_numbers with semicolon-separated segments (each <= 120 chars, total <= 300 chars) pass semantic quality audit, while unsegmented >120 char or total >300 char doc_numbers fail."""
        from wlh.storage.cas import Objects

        objs = Objects()
        md1_hash = objs.put(
            (
                "# 事業廢棄物再利用管理辦法修正條文\n\n"
                "第一條 本辦法依廢棄物清理法第三十九條第二項規定訂定之。"
                "為促進資源循環利用並落實跨部會共同管理機制，特會銜修正本辦法。"
            ).encode("utf-8")
        )
        md2_hash = objs.put(
            (
                "# 共通性事業廢棄物再利用管理辦法\n\n"
                "第一條 本辦法依廢棄物清理法第三十九條第二項規定訂定之。"
                "共通性事業廢棄物之再利用種類及管理方式依附表規定辦理。"
            ).encode("utf-8")
        )
        joint_order_doc_number = (
            "環署廢字第1101119523C號;台內營字第1100809362號;國規委會字第1100128073號;"
            "臺教資（六）字第1100024744A號;經工字第11004601330號;交總字第11000080671號;"
            "衛部醫字第1101601340號;科部產字第1100022113B號;農牧字第1100710632A號;"
            "台財庫字第11000048721號;通傳基礎字第11000294361號 令"
        )
        rows = [
            {
                "source_id": "dd-127",
                "resource_id": "res-tw-joint-1",
                "row_index": 1,
                "role": "primary",
                "title": "事業廢棄物再利用管理辦法修正條文",
                "markdown_file": f"joint1_{'a' * 8}_{md1_hash[:8]}.md",
                "doc_type": "法律法规",
                "acquired_at": "2026-10-31 08:00:00",
                "original_file": None,
                "volume_order": None,
                "doc_number": joint_order_doc_number,
                "issuer": "行政院環境保護署",
                "published_at": "2021-12-20",
                "effective_at": "2021-12-20",
                "expired_at": None,
                "status": "有效",
                "source_name": "行政院公報資訊網",
                "detail_url": "https://gazette.nat.gov.tw/egFront/detail1.do",
                "source_record_hash": "a" * 64,
                "markdown_hash": md1_hash,
                "original_hash": None,
            },
            {
                "source_id": "dd-127",
                "resource_id": "res-tw-joint-2",
                "row_index": 1,
                "role": "primary",
                "title": "共通性事業廢棄物再利用管理辦法",
                "markdown_file": f"joint2_{'b' * 8}_{md2_hash[:8]}.md",
                "doc_type": "法律法规",
                "acquired_at": "2026-10-31 08:01:00",
                "original_file": None,
                "volume_order": None,
                "doc_number": "環署廢字第1101119523號",
                "issuer": "行政院環境保護署",
                "published_at": "2021-12-21",
                "effective_at": "2021-12-21",
                "expired_at": None,
                "status": "有效",
                "source_name": "行政院公報資訊網",
                "detail_url": "https://gazette.nat.gov.tw/egFront/detail2.do",
                "source_record_hash": "b" * 64,
                "markdown_hash": md2_hash,
                "original_hash": None,
            },
        ]
        report = audit_delivery_rows(rows, objects=objs, require_multi_resource=True)
        assert report["passed"] is True
        assert report["violations"] == []

        unsegmented_polluted_doc_number = (
            "智著字第0916000641號附件：一、按著作權法(以下稱本法)第七條之一第一項規定："
            "表演人對既有著作或民俗創作之表演，以獨立之著作保護之。表演之保護，對原著作之著作權不生影響。"
            "準此，利用人如欲利用他人享有著作權之詞曲著作改作並錄製成錄音著作，應徵得詞曲著作財產權人之同意"
        )
        overlong_semicolon_doc_number = ";".join(
            f"環署廢字第110111952{i:02d}號" for i in range(25)
        )
        for bad_doc_no in (unsegmented_polluted_doc_number, overlong_semicolon_doc_number):
            bad_rows = [
                {
                    **rows[0],
                    "doc_number": bad_doc_no,
                },
                rows[1],
            ]
            bad_report = audit_delivery_rows(
                bad_rows, objects=objs, require_multi_resource=True
            )
            assert bad_report["passed"] is False
            bad_codes = set(
                bad_report.get("violation_codes")
                or [v["code"] for v in bad_report["violations"]]
            )
            assert "polluted_or_malformed_doc_number" in bad_codes

