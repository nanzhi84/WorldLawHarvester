# Changelog

本项目的所有重要版本变更、新特性汇总与修复记录均归档于此。

## [Unreleased]

### Changed

- 日本来源继续按 CSV 原入口适配：增加完整目录分页与身份对账、必需附件原子交付及二进制文献目录标题证据核验。有限回放和预算结束不再用作整源完成的证据，当前范围及阻断结论见[来源台账](docs/runbooks/source-adaptation-matrix.md)。
- 法律 XML 与 HTML 保留混合文本、上下标、脚注及可验证表格；生效日期仅来自对应法律版本的明确证据，不用公布、裁判或汇编日期补齐。PDF 增加原生乱码、复杂布局及未验证图片阻断。
- 移除将日本官报目录当作正文的旧配置。NTA 的矢量图与公式、官报竖排扫描 OCR 等仍保留明确验收缺口，不以局部样本通过宣称整源完成。

### Added
- **全量 40 源实网收尾闭环与全局快照发布（Issue #4 Batch 4）**：在全新独立验证库 `wlh_issue4_full` 完成全量 40 个已适配来源实网采集与离线重解析（100% complete，离线重解析 external_requests=0，除单行本法典外其余集合型源均满足 resources >= 10）；完成全局快照创建、发布与只读 Canonical API（`GET /results?projection=canonical` 与 `GET /files/{sha256}`）实测核验。
- **固定入口去时间化与动态目录发现（Issue #4 Batch 3）**：消除 `entry_urls` 中的硬编码年份、日期、期号与单章限制（dd-501 国务院公报总目录动态重定向、dd-559 eCFR `titles.json` 结构索引跨 Title 自动展开、dd-560 Open States `sort=updated_desc` 倒序检索、dd-565 加拿大最高法院无年份 Decisia 入口、dd-562 美国最高法院 `opinions.aspx` 动态 Term 跨期回落、dd-520 日本国税厅通达跨章条文发现）。
- **交付数据质量修复与契约升级（Issue #4 Batch 2）**：修复 dd-455、dd-575、dd-501、dd-549 等源标题乱码、文号粘连与非正文页面过滤；升级 `wlh.pdf-scan.v2`（文档级签名块扫描判定）与 `wlh.quality.v2`（TCVN3 字符集白名单扩展），明确 dd-040 目录型数据形态说明。
- **源名实对齐与越南草案分拆（Issue #4 Batch 1）**：规范 dd-512 为越南国家法律数据库正式法律源，分拆新建 dd-575 承接国会在线草案门户（duthaoonline）；扩展通用 cookie 挑战共享机制。
- **配置驱动收敛与回放门禁清零（Issue #3）**：消除代码层针对特定站点的临时硬编码分支，统一基于通用适配器规范与 YAML 声明配置驱动；构建反单篇作弊门禁与全量生产配置本地真实样本黑盒回放体系。
- **多法域高优来源规模化适配（Issue #2）**：分批次完成 40 个全球高优法域来源实网适配与离线 reparse 契约对齐，全面覆盖中国、越南、美国、英国、德国、日本、加拿大、澳大利亚、新西兰及国际组织核心公报、立法与判例。
- 完成 `WorldLawHarvester` (`wlh`) v1 核心采集、CAS 归档、多模态解析（Office/TCVN3/PDF 扫描探测与 `br-ocr-v1` + `tesseract` 主备自动切换）、离线零外网 `reparse`、16 列标准模板投影与只读结果 API 实现及实网矩阵验收（Issue #1）。
- 建立 [`docs/runbooks/source-adaptation-matrix.md`](docs/runbooks/source-adaptation-matrix.md) 作为全球 574 个来源（`dd-001`–`dd-574`）的唯一事实源登记表，直接对齐 `资料类型` 与 `来源名称`。
- 初始化项目 Universal Harness 目录结构与文档规范。
