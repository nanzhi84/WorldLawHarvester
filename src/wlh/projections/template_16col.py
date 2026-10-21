"""Pure projection into the public Chinese delivery template."""

from datetime import date

from wlh.domain.document import timestamp

COLUMNS = (
    "资料名称",
    "解析后文件",
    "资料类型",
    "采集时间",
    "原始文件",
    "文件顺序",
    "文号/案号",
    "发布机构",
    "发布日期",
    "生效日期",
    "失效日期",
    "状态",
    "来源名称",
    "来源详情页",
    "来源记录号",
)


def row_values(row):
    """One delivery row as the template columns (strings; missing values empty)."""

    def day(value):
        if value is None:
            return ""
        return value.isoformat() if isinstance(value, date) else str(value)

    return {
        "资料名称": row["title"],
        "解析后文件": row["markdown_file"],
        "资料类型": row["doc_type"],
        "采集时间": timestamp(row["acquired_at"]),
        "原始文件": row["original_file"] or "",
        "文件顺序": str(row["volume_order"]) if row["volume_order"] else "",
        "文号/案号": row["doc_number"] or "",
        "发布机构": row["issuer"] or "",
        "发布日期": day(row["published_at"]),
        "生效日期": day(row["effective_at"]),
        "失效日期": day(row["expired_at"]),
        "状态": row["status"] or "",
        "来源名称": row["source_name"] or "",
        "来源详情页": row["detail_url"] or "",
        "来源记录号": row["source_record_hash"],
    }


def project_template_16col(row):
    return {
        **row_values(row),
        **{
            k: row[k]
            for k in (
                "source_id",
                "resource_id",
                "version_id",
                "row_index",
                "original_hash",
                "markdown_hash",
            )
        },
        "row_number": row["row_index"],
    }
