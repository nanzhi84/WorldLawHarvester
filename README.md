<p align="center">
  <h1 align="center">WorldLawHarvester (<code>wlh</code>)</h1>
</p>

<p align="center"><strong>跨法域公开法律法规、司法判例与政府公报受控采集、归档与结构化引擎</strong></p>

<p align="center">
  <code>WorldLawHarvester</code> 面向全球多法域构建可审计的法律文献采集与归档底座。系统坚持“先 S3 CAS 原件归档校验、后离线提取与质量门禁”，原生桥接五大开源法律采集生态（<code>Juriscraper</code>、<code>oldp-ingestor</code>、<code>Open States</code>、<code>OpenCaseLaw</code>、<code>World Wide Law</code>）并对接内网 <code>br-ocr-v1</code> 版面 OCR 服务，第一版极简聚焦 16 列核心采集字段（<code>wlh.document.v1</code>），通过保留的可插拔投影器按需输出下游视图。
</p>

## Core ideas

- **受控获取与五大开源生态桥接（Adapter Bridges）**：彻底弃用臃肿的 `crawl4ai`，以轻量受控 HTTP / CDP 浏览器底座先将所有外部原始字节归档至私有 S3 CAS 并校验 SHA-256；原生提供五大桥接器无缝复用 `Juriscraper`、`oldp-ingestor`、`Open States`、`OpenCaseLaw` 与 `World Wide Law` 的站点解析逻辑，与原生 Adapter 统一汇聚为唯一的 16 列对标候选结构 `RawRecord`，支持零外部网络离线重解析（`wlh reparse`）。
- **聚焦 16 列核心采集模型 + 可插拔投影器**：采集层只负责把数据干净抓下来，核心表（`wlh.document_row`）用英文标准字段存储 16 列所需属性，拒绝过度设计；同时保留 `src/wlh/projections/` 可插拔投影设计，默认通过 `template_16col` 对齐 16 列中文上传模板。
- **内网 `br-ocr-v1` 与 Office 深度提取管道**：复用并内化 [office_text_extract.py](docs/reference/office_text_extract.py) 与 [production_document_parse.py](docs/reference/production_document_parse.py)，内置容器魔数探测、Office 结构化直提、越南语 TCVN3 历史字体逐行恢复、基于 `PyMuPDF` (`fitz`) 的 PDF 逐页墨迹扫描件判定、内网 `br-ocr-v1` 版面 OCR（带 S3 CAS 结果缓存，兼顾 `tesseract` 备选）与多语种参数化的客观质量门禁（`quality_check`）。

## Get started

```bash
# 运行仓库结构、架构图约定与文档死链巡检
yoryon-harness doctor
```

## Learn more

- **协作规范与领域红线**：[AGENTS.md](AGENTS.md) · [PROJECTS.md](PROJECTS.md)
- **文档导航与全局架构**：[文档维护规范](docs/README.md) · [全局架构现状](docs/architecture/overview.md) · [通用法律模型与投影架构](docs/architecture/canonical-legal-model.md)
- **首批实施计划与架构决策**：[Issue #1 首批架构与分阶段实施计划](docs/spec/issue#1/spec.md) · [ADR-0001](docs/adr/0001-canonical-legal-domain-and-pluggable-projectors.md) · [ADR-0002](docs/adr/0002-controlled-cas-acquisition-and-offline-reparse.md) · [ADR-0003](docs/adr/0003-multi-adapter-bridges-and-intranet-ocr-stack.md)
- **外部参考与版本记录**：[参考资料索引](docs/reference/INDEX.md) · [CHANGELOG.md](CHANGELOG.md)
