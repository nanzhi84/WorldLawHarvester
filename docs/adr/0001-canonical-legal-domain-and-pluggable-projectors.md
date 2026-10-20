# ADR-0001: 聚焦 16 列核心采集模型与保留可插拔投影器

- **状态 (Status)**：Accepted
- **日期 (Date)**：2026-10-21

## 1. 背景与上下文 (Context)

在设计 `WorldLawHarvester` (`wlh`) 的第一版数据模型时，面临两个方向的权衡：
1. **避免过度设计**：采集层的核心职责是把公开法律法规、公报和判例稳定抓取、归档并提取出干净的 Markdown 正文。如果在第一版引入庞大的比较法本体论（如跨国效力位阶枚举、多级法域、上位法引用与立改废图谱、法条语法树 AST、判例合议庭拆解），会大幅增加每个 Adapter 的开发负担，而这些深层语义抽取本应由下游（LLM / 知识图谱 / RAG）基于 `.md` 正文完成。
2. **避免核心数据库表直接硬编码中文列名**：下游知识库当前只需要 [法规案例元数据上传模板](../reference/法规案例元数据上传模板.xlsx) 的 16 列数据，但如果在 PostgreSQL 核心表中直接用中文列名建表，代码可维护性差且不便未来扩展其他导出格式。

## 2. 决策方案 (Decision)

1. **第一版核心模型严格聚焦 16 列所需字段，砍掉一切过度设计的法学本体字段**：
   - 上游统一候选结构（`RawRecord`）、S3 CAS 结果对象（`wlh.document.v1`）与 PostgreSQL 关系表（`wlh.document_row`）**只存储与 16 列模板一一对应的核心字段**（`title`、`markdown_file`/`markdown_hash`、`doc_type`、`acquired_at`、`original_file`/`original_hash`、`volume_order`、`document_number`、`issuer`、`published_at`、`effective_at`、`expired_at`、`status`、`source_name`、`detail_url`、`source_record_hash`）。
   - 不在采集层设计或抽取 `citations`、`amends`、`repeals`、`legal_hierarchy`、`structure_ast` 或复杂的 `case_meta`。
2. **保留轻量可插拔投影器层（`src/wlh/projections/`）**：
   - 数据库 `wlh.document_row` 表使用干净的英文标准列名存储。
   - 通过 `src/wlh/projections/template_16col.py` 将 `document_row` 的英文字段无损投影为 16 列中文模板格式（`GET /results` 默认输出）。

## 3. 影响与后果 (Consequences)

- **正面收益**：
  - 采集层负担极轻，所有原生 Adapter 和五大开源项目 Bridge 只需抓取并填充 16 列对应的基础字段即可快速投产。
  - 保留了 `Projector` 投影层，兼顾了核心代码/数据库英文命名的整洁性与下游 16 列中文模板的 100% 兼容。
- **潜在代价与约束**：
  - 正文内部的法条引用、立改废关系与细粒度结构切块交由下游系统消费 `.md` 文件处理，不在爬虫层耦合。
