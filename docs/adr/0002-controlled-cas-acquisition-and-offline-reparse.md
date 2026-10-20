# ADR-0002: 受控 CAS 原件归档、四重指纹版本控制与零网络离线重解析

- **状态 (Status)**：Accepted
- **日期 (Date)**：2026-10-21

## 1. 背景与上下文 (Context)

政府公报、立法数据库与法院公开网普遍存在以下工程挑战：
1. **外部来源不稳定且不可逆**：政务网站常出现临时限流、会话过期、ASP.NET `__doPostBack` 翻页状态漂移、页面改版或历史附件下架。如果在抓取同时直接流式解析入库而不落盘原始字节，一旦解析规则有误，往往无法复现当时的原始响应。
2. **法律文档格式异构且历史遗留问题多**：同一来源常混杂 HTML 详情页、伪装成 `.doc` 的 `.xls`/`.rtf`、嵌套 `.zip`、越南语 TCVN3 历史字体编码文档，以及“仅含数字签章文字层但正文全为扫描图像”的混合 PDF。解析器、编码恢复算法与 OCR 质量门禁需要持续迭代调优。
3. **列表翻页与外围 DOM 噪音易引发虚假版本**：列表页顺序变化、分页参数调整或详情页侧边栏动态推荐变动，若直接对整页响应算哈希，会导致大量未发生实质内容变更的法律文档重复生成新版本。

## 2. 决策方案 (Decision)

1. **受控外部获取（`Acquisition`）与 S3 CAS 强校验**：
   - 所有外部输入（`http` 实体字节与经 CDP `Fetch.requestPaused` 逐跳拦截的 `rendered_dom`）必须先写入私有 S3 CAS，并立即执行 `Objects.get()` 读回校验 SHA-256，确认一致后在短事务内记录 `state = 'archived'` 的 `Acquisition`。
   - 严禁在持有数据库连接事务期间执行外部网络 I/O、浏览器渲染或重型文档提取；所有状态更新均受 PostgreSQL advisory lock 与 `attempt_epoch` 围栏保护。
2. **四重指纹隔离外围噪音**：
   - 区分完整原件归档哈希（`archive_ref`，用于审计与重放）与文档局部语义切片哈希（`slice_hash` + `semantic_locator`，用于版本比较）。
   - 每个 `Version` 同时记录 `input_fingerprint`、`parser_fingerprint`、`version_fingerprint` 与 `content_hash`，仅当实质输入切片或解析产物发生变化时才推进版本序列。
3. **零来源网络离线重解析（`wlh reparse`）**：
   - 当升级法律条文 AST 解析器、TCVN3 字体恢复规则、PDF 扫描判定阈值或 OCR 模型时，通过 `wlh reparse` 冻结当前版本已归档的 CAS 输入，在传输层硬性锁死 `external_requests = 0` 的前提下离线重算并生成新版本。

## 3. 影响与后果 (Consequences)

- **正面收益**：
  - 每一条入库法规和判例均具备从 API 交付行 -> `Version` -> `wlh.legal_document.v1` -> `wlh.inputs.v1` -> `Acquisition` -> S3 CAS 原始字节的完整可验证证据链。
  - 解析规则与 OCR 迭代无需重复请求脆弱的外部政务网站，彻底杜绝因重抓导致的封禁或原件丢失。
- **潜在代价与约束**：
  - 存储开销增加（所有参与解析的原始响应与中间标准文档均持久化在 S3 CAS 中），且任何 Adapter 都必须严格遵循两阶段（先规划请求归档、后从 CAS 读取解析）编程模型。
