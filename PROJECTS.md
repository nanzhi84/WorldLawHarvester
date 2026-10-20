# PROJECTS.md — WorldLawHarvester (`wlh`) 项目特异规范与领域红线

## 1. 项目定位与命名约定
- **项目名称**：`WorldLawHarvester`（跨法域公开法律法规、司法判例与政府公报受控采集、归档与结构化引擎）。
- **统一标识符**：
  - Python 顶层包名：`wlh`（源码位于 `src/wlh/`）
  - CLI 命令名：`wlh`（`wlh init`、`wlh collect`、`wlh recheck`、`wlh reparse`、`wlh worker`、`wlh snapshot`、`wlh serve`）
  - PostgreSQL Schema：`wlh`

## 2. 技术栈与基础设施约束
- **运行环境与核心依赖**：
  - Python >= 3.12（使用 `uv` 管理虚拟环境与锁文件 `uv.lock`）。
  - **坚决弃用 `crawl4ai`**：受控 HTTP 传输采用 `httpx` / `aiohttp` + `protego`，动态网页采用 `playwright`（纯 CDP `Fetch.requestPaused` 逐跳拦截），HTML/XML 解析采用 `lxml` + `selectolax` + 自研确定性法律 DOM-to-Markdown/AST 转换器（见 [ADR-0003](docs/adr/0003-multi-adapter-bridges-and-intranet-ocr-stack.md)）。
- **核心存储**：
  - **PostgreSQL**：唯一关系型状态与元数据账本（Schema 为 `wlh`，单一干净基线 `src/wlh/sql/schema.sql`，通过 advisory lock 与 `attempt_epoch` 保证并发围栏，异步队列采用 `procrastinate`）。
  - **S3 兼容内容寻址存储（CAS，本地使用 MinIO）**：保存所有外部原始响应字节（HTML、渲染 DOM、PDF、DOC/DOCX/RTF、XLS/XLSX、JSON、XML、ZIP）、输入清单（`wlh.inputs.v1`）、核心文档对象（`wlh.document.v1`）及归一化 Markdown（`.md`）。
- **对象存储访问红线**：根目录 `minio/`（若本地挂载）仅供容器服务管理，严禁代码或脚本直接读写本地文件系统下的 `minio/` 目录；所有对象存取必须统一走 S3 CAS API（写入后立即读回复算 SHA-256）。

## 3. 核心领域红线（不可违反的不变量）

### 3.1 第一版聚焦 16 列核心采集模型与保留可插拔投影器
- **只存 16 列所需核心字段，拒绝过度设计**：采集层只负责把数据稳定抓取下来，系统内部（`RawRecord`、`wlh.document.v1`、数据库表 `wlh.document_row`）只存储与 [法规案例元数据上传模板](docs/reference/法规案例元数据上传模板.xlsx) 16 列直接对应的英文标准字段（`title`、`markdown_file`/`markdown_hash`、`doc_type`、`acquired_at`、`original_file`/`original_hash`、`volume_order`、`document_number`、`issuer`、`published_at`、`effective_at`、`expired_at`、`status`、`source_name`、`detail_url`、`source_record_hash`），不做跨国法律位阶、法条语法树 AST 或立改废引用抽取等过度设计。
- **保留可插拔下游投影器（Projector）**：核心数据库表使用英文标准列名，通过 `src/wlh/projections/template_16col.py` 将核心行投影为 16 列中文表格格式（见 [ADR-0001](docs/adr/0001-canonical-legal-domain-and-pluggable-projectors.md)）。

### 3.2 原生多 Adapter 桥接与先归档后解析（`reparse`）
- **统一候选结构（`RawRecord`）与五大开源采集生态桥接（Adapter Bridges）**：在 `src/wlh/adapters/` 中原生支持自有站点 Adapter、官方标准立法 XML 以及桥接 [公开数据源和开源采集项目调研.md](docs/reference/公开数据源和开源采集项目调研.md) 中评估的五大开源项目（`Juriscraper`、`oldp-ingestor`、`Open States`、`OpenCaseLaw`、`World Wide Law`）。所有桥接脚本通过 `CasSessionProxy` 拦截外部请求并先归档至 S3 CAS，且所有原生 Adapter 与五大开源 Bridge 统一输出唯一的 16 列对标结构 `RawRecord`（含 `1..N` 个 `RawUnitSource`）。
- **零网络离线重解析（`reparse`）**：`wlh reparse` 冻结已归档 CAS 输入，在外部来源网络请求 `external_requests = 0` 的强约束下利用新解析器、新条文结构化规则或内网 OCR 服务（优先命中 S3 CAS 中已缓存的派生 OCR JSON）重算文档版本。
- **外部 I/O 与数据库事务隔离**：进行网络请求、浏览器渲染、LibreOffice 转换或调用内网 `br-ocr-v1` 轮询期间，严禁持有数据库打开的事务；所有状态提交在短事务内配合 `attempt_epoch` 围栏完成。

### 3.3 复用参考提取脚本、内网 `br-ocr-v1` 服务与客观质量门禁
- **直接复用并工程化参考脚本**：将 [office_text_extract.py](docs/reference/office_text_extract.py) 与 [production_document_parse.py](docs/reference/production_document_parse.py) 的核心逻辑原生重构进 `src/wlh/extraction/`：
  - **容器真实类型探测与 Office 直提**：按魔数与容器结构识别真实格式（识别伪装为 `.doc` 的 `.xls`/`.xlsx`/`.rtf` 或嵌套 `.zip`），优先直接结构化提取（`office_text_policy = "prefer"`）；对二进制 `.doc` 优先经 Headless LibreOffice `txt:Text` 导出并执行越南语 TCVN3 历史字体逐行恢复（避免容器缺 `.VnTime` 字体导致直接转 PDF 乱码），无有效文本时再转 PDF 进入 PDF/OCR 管道。
  - **原生直提优先 + OCR 主备自动兜底（先尝试内网 `br-ocr-v1` -> 不可达自动切本机 `tesseract`）**：基于 `PyMuPDF` (`fitz`) 逐页统计 `native_text_chars`、`embedded_images` 与 `0.5x` 灰度 `ink_ratio`（`< 210` 像素占比）判定 `suspected_scan`。含原生文字层的页面直接由 `PyMuPDF` 提取文本；当存在扫描页需调用 OCR 时，**先尝试内网 `br-ocr-v1`（`http://192.168.160.67:7555/api/v1`，短连接超时快速探测，成功则缓存入 S3 CAS），若当前无内网环境连接不可达或失败，自动无缝兜底切到本机 `tesseract` 代管**。
  - **按语种参数化的客观质量门禁（`quality_check`）**：对解析/OCR 产物执行页级与篇级客观核验（跨法域通用的页码映射、扫描页缺字、原生页丢字、空白页伪影、表格结构 `table_body` 校验、`bbox` 阅读顺序反转，以及 `language == "vi"` 专属的 TCVN3 乱码、非越语字符过滤与越南公文落款签章检测），产出确定性路由（`accept_ocr`、`accept_with_warning`、`vision_reparse_pages`、`legacy_font_encoding_recovery`）。未通过必选质量门禁的文档严禁生成成功 `Version`。

### 3.4 来源身份与稳定编号（`dd-xxx`）
- [尽职调查核验入口地图](docs/reference/尽职调查核验入口地图.md) 是所有采集来源身份与分类的唯一登记表。
- 新增或变更来源必须先在地图登记稳定 `dd-xxx` ID（从最大序号递增补齐三位，末尾追加，严禁重排、回填或复用），Source 配置统一命名为 `sources/dd-xxx.yaml`，文档引用统一链接至地图锚点（`#dd-xxx`）。
