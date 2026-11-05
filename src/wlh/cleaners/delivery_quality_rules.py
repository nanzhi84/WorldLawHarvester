"""Delivery semantic quality rules, merged by language and jurisdiction.

Keep rule data here and loading/auditing behavior in delivery_quality.py.
"""

from typing import Final, TypedDict


class SignatureStubRule(TypedDict):
    primary: str
    secondary: list[str]


class RuleSection(TypedDict, total=False):
    languages: list[str]
    boilerplate_titles: list[str]
    bare_doc_number_labels: list[str]
    placeholder_patterns: list[str]
    running_header_regexes: list[str]
    salutation_prefix_regexes: list[str]
    signature_stub_rules: list[SignatureStubRule]
    supplementary_heading_prefixes: list[str]


class RuleRegistry(TypedDict):
    common: RuleSection
    languages: dict[str, RuleSection]
    jurisdictions: dict[str, RuleSection]


DELIVERY_QUALITY_RULES: Final[RuleRegistry] = {
    "common": {
        "boilerplate_titles": [
            "contents",
            "table of contents",
            "index",
            "home",
            "untitled",
            "document",
            "download",
            "download pdf",
            "download file",
            "attachment",
        ],
        "bare_doc_number_labels": ["no\\.?"],
        "placeholder_patterns": [],
        "running_header_regexes": [],
        "salutation_prefix_regexes": [],
        "signature_stub_rules": [
            {"primary": "signer:", "secondary": ["certificate id:", "signed at:"]}
        ],
        "supplementary_heading_prefixes": [],
    },
    "languages": {
        "en": {
            "boilerplate_titles": [
                "contents",
                "table of contents",
                "index",
                "home",
                "untitled",
                "document",
                "download",
                "download pdf",
                "download file",
                "attachment",
            ],
            "bare_doc_number_labels": ["no\\.?"],
            "signature_stub_rules": [
                {"primary": "signer:", "secondary": ["certificate id:", "signed at:"]}
            ],
        },
        "zh": {
            "boilerplate_titles": [
                "目录",
                "首页",
                "网站首页",
                "附件",
                "附件下载",
                "点击下载",
                "下载附件",
                "查看详情",
                "详情",
            ],
            "bare_doc_number_labels": ["文号", "发文字号", "案号"],
            "placeholder_patterns": ["加载失败", "页面不存在", "暂无数据", "访问受限", "系统繁忙"],
            "supplementary_heading_prefixes": ["附則", "附 則"],
        },
        "ja": {"supplementary_heading_prefixes": ["附則", "附 則"]},
        "vi": {
            "boilerplate_titles": [
                "tải file",
                "tải file đính kèm",
                "file đính kèm",
                "tải về",
                "xem chi tiết",
                "chi tiết",
            ],
            "bare_doc_number_labels": ["số", "số\\s+ký\\s+hiệu"],
            "placeholder_patterns": [
                "bạn chưa đăng nhập thành viên",
                "văn bản này đang cập nhật nội dung",
                "đây là tiện ích dành cho tài khoản tiêu chuẩn hoặc nâng cao",
                "vui lòng xem nội dung chi tiết trong file đính kèm",
            ],
            "running_header_regexes": ["(?im)^\\s*công\\s+báo\\s*/\\s*số\\s+\\d+.*$"],
            "salutation_prefix_regexes": ["^bộ\\s+tài\\s+chính\\s*[.:,-]\\s*"],
            "signature_stub_rules": [{"primary": "ký bởi", "secondary": ["chứng thư", "ngày ký"]}],
        },
        "de": {
            "boilerplate_titles": [
                "inhalt",
                "inhaltsverzeichnis",
                "startseite",
                "herunterladen",
                "anhang",
            ],
            "bare_doc_number_labels": ["nr\\.?", "aktenzeichen"],
        },
        "fr": {
            "boilerplate_titles": [
                "sommaire",
                "table des matières",
                "accueil",
                "télécharger",
                "annexe",
            ],
            "bare_doc_number_labels": ["n[o°]\\.?"],
        },
        "it": {
            "boilerplate_titles": ["indice", "sommario", "scarica", "allegato"],
            "bare_doc_number_labels": ["n\\.?"],
        },
        "ko": {
            "boilerplate_titles": ["목차", "홈", "다운로드", "첨부파일"],
            "bare_doc_number_labels": ["문서번호", "사건번호"],
        },
    },
    "jurisdictions": {
        "VN": {
            "languages": ["vi"],
            "placeholder_patterns": [
                "bạn chưa đăng nhập thành viên",
                "văn bản này đang cập nhật nội dung",
                "đây là tiện ích dành cho tài khoản tiêu chuẩn hoặc nâng cao",
                "vui lòng xem nội dung chi tiết trong file đính kèm",
            ],
            "running_header_regexes": ["(?im)^\\s*công\\s+báo\\s*/\\s*số\\s+\\d+.*$"],
            "salutation_prefix_regexes": ["^bộ\\s+tài\\s+chính\\s*[.:,-]\\s*"],
        },
        "CN": {"languages": ["zh"]},
        "TW": {"languages": ["zh"], "supplementary_heading_prefixes": ["附則", "附 則"]},
        "HK": {"languages": ["en", "zh"], "supplementary_heading_prefixes": ["附則", "附 則"]},
        "JP": {"languages": ["ja"], "supplementary_heading_prefixes": ["附則", "附 則"]},
        "KR": {"languages": ["ko"]},
        "US": {"languages": ["en"]},
        "GB": {"languages": ["en"]},
        "CA": {"languages": ["en", "fr"]},
        "SG": {"languages": ["en"]},
        "NZ": {"languages": ["en"]},
        "ZA": {"languages": ["en"]},
        "DE": {"languages": ["de"]},
        "CH": {"languages": ["de", "fr", "it"]},
        "GL": {"languages": ["en"]},
    },
}
