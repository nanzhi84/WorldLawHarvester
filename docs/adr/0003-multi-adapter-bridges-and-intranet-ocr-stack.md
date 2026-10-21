# ADR-0003: 弃用 Crawl4AI、原生融合五大开源采集生态与内网 `br-ocr-v1` 解析栈

- **状态 (Status)**：Accepted
- **日期 (Date)**：2026-10-21

## 1. 背景与上下文 (Context)

在构建 `WorldLawHarvester` (`wlh`) 时，需要在三个关键技术维度做出明确选型：
1. **是否继续保留 `crawl4ai`**：在前序项目（SEAL）后期，HTTP 获取已由自研 `transport.py` 接管，浏览器渲染已由 `playwright` + CDP `Fetch.requestPaused` 逐跳拦截实现，唯一调用 `crawl4ai` 的地方仅剩 `markdown.py` 中的 `DefaultMarkdownGenerator`（5 行 HTML 转 Markdown 代码）。但保留 `crawl4ai[pdf]` 引入了 LiteLLM、Chroma 等上百个无关重型传递依赖，且存在写全局 `~/.crawl4ai` 缓存目录的副作用，对法条表格与层级的转换也不够确定。
2. **如何复用全球开源法律采集生态**：根据 [公开数据源和开源采集项目调研.md](../reference/公开数据源和开源采集项目调研.md)，`Juriscraper`（273+ 美国法院模块）、`oldp-ingestor`（德国与欧盟法院/法规 Provider）、`Open States`（美国各州立法 Scraper）、`OpenCaseLaw`（瑞士联邦/州法院与 Fedlex）以及 `World Wide Law / legal-sources`（110+ 国 960+ 脚本，如 `VN/CongBao`）已经沉淀了大量站点的枚举、分页与 DOM/XML 字段抽取逻辑，但它们各自缺乏统一的 S3 CAS 原件归档、事务围栏、OCR 管道与统一法律领域模型。
3. **如何原生集成生产级法律文档提取与内网 OCR 服务**：仓库已沉淀经过验证的参考实现 [production_document_parse.py](../reference/production_document_parse.py)（对接内网 `br-ocr-v1` 服务 `http://192.168.160.67:7555/api/v1`）与 [office_text_extract.py](../reference/office_text_extract.py)（Office 容器魔数识别、DOC/DOCX/RTF/XLS 直提、越南语 TCVN3 历史字体逐行恢复与法律层级还原）。

## 2. 决策方案 (Decision)

1. **运行环境基线与彻底移除 `crawl4ai` 依赖**：
   - 运行环境统一要求 Python >= 3.12（使用 `uv` 管理虚拟环境与锁文件 `uv.lock`）。
   - 受控获取层采用轻量级 `httpx` / `aiohttp` + `protego`（HTTP 传输）与 `playwright`（纯 CDP `Fetch.requestPaused` 拦截渲染 DOM）。
   - HTML/XML 解析与 Markdown 转换采用 `lxml` + `selectolax` + 自研确定性法律 DOM-to-Markdown 转换器，消除隐式缓存副作用并保证 100% 确定性输出。

2. **原生设计统一 `RawRecord`（聚焦 16 列）候选数据结构与五大开源生态 Bridge 架构（`src/wlh/adapters/`）**：
   - 所有原生站点 Adapter、标准立法 XML 适配器与五大开源生态桥接器统一共享受控会话代理（`CasSessionProxy`）将外部请求先归档至 S3 CAS，并**统一输出仅包含 16 列所需核心字段的中间候选结构 `RawRecord`（含 `1..N` 个 `RawUnitSource`）**，由下游单轨流水线统一完成附件补齐、文档提取与版本入库：
     - **`JuriscraperBridge`（`juriscraper_bridge.py`，BSD-2-Clause）**：接管 `OpinionSite` / `OralArgumentSite` 的 `_download()` 走 `CasSessionProxy`，提取案名、案号、裁判日与法院 PDF 链接，映射为 `RawRecord`。
     - **`OldpProviderBridge`（`oldp_bridge.py`，MIT）**：桥接 `oldp-ingestor` 的 `Law` / `Case` Provider（德国联邦/州与 EUR-Lex），将原始 XML/HTML 纳入 CAS 并映射为 `RawRecord`。
     - **`OpenStatesBridge`（`openstates_bridge.py`，GPL-3.0，可选依赖隔离）**：桥接 `Open States` 的 `Scraper` 与 `Bill` 对象，将法案版本与附件映射为 `RawRecord` 的 `units`。
     - **`OpenCaseLawBridge`（`opencaselaw_bridge.py`，MIT）**：独立桥接 `OpenCaseLaw` 的瑞士法院与 Fedlex 采集器，提取标题、案号、法院、发布日及 PDF/全文映射为 `RawRecord`。
     - **`WorldWideLawBridge`（`worldwidelaw_bridge.py`，AGPL-3.0，受控子进程/外部脚本隔离）**：面向 `World Wide Law`（如 `VN/CongBao`）的 `fetch_all` / `fetch_updates` 生成器，通过 `CasSessionProxy` 受控归档并将记录及 DOCX/PDF 链接转化为 `RawRecord`。
     - **`StructuredLegalXMLAdapter`（`structured_xml.py`）**：原生支持 Akoma Ntoso (AKN 3.0)、USLM、台湾全国法规 XML 与日本 e-Gov XML，提取 16 列基础元数据与正文 Markdown 产出 `RawRecord`。

3. **原生直提优先、OCR 仅作扫描件兜底（无内网默认本地 `tesseract` 代管 + 内网 `br-ocr-v1` 可切/自动降级）**：
   - 直接复用并重构 [production_document_parse.py](../reference/production_document_parse.py) 与 [office_text_extract.py](../reference/office_text_extract.py) 进入核心提取层，确立“直提为主、OCR 仅作兜底”原则：
     - **第一级（容器真实类型探测、Office 直提与 `.doc -> txt:Text -> TCVN3` 编码保护）**：按魔数与容器结构识别真实格式（识别伪装为 `.doc` 的 `.xls`/`.xlsx`/`.rtf`/`.html` 或嵌套 `.zip`，默认 `office_text_policy = "prefer"`），对 `.docx`/`.rtf`/`.xls`/`.xlsx` 优先执行结构化直提；对二进制 `.doc` 优先通过隔离 Headless LibreOffice 执行 `txt:Text` 导出并运行 `normalize_detected_tcvn3` 逐行恢复越南语 TCVN3 历史字体（避免容器缺 `.VnTime` 字体导致直接转 PDF 乱码），仅在无有效文本时才转 PDF 进入 PDF/OCR 管道。
     - **第二级（`PyMuPDF` 原生文本直提与逐页扫描判定 `analyze_pdf`）**：统一采用 **`PyMuPDF` (`fitz`)** 逐页计算 `native_text_chars`、`embedded_images` 与 `0.5x` 灰度 `ink_ratio`（`< 210` 像素占比）。有原生文字层的页面直接提取文本，不走 OCR；仅将 `native_text_chars < 30` 且 `ink_ratio > 0.003` 的页面标记为 `suspected_scan` 送入 OCR 兜底。
     - **第三级（OCR 引擎主备自动兜底：先尝试内网 `br-ocr-v1`，不可达自动切本机 `tesseract`）**：当需要调用 OCR 时，不做静态二选一路由，而是先尝试请求内网 `br-ocr-v1`（`http://192.168.160.67:7555/api/v1`，设 3 秒快速连接超时，成功则缓存派生 OCR JSON 至 S3 CAS）；若当前处于无内网环境连接失败或服务不可用，自动无缝 fallback 切到本机受控 `tesseract` 代管识别。
     - **多语种参数化客观质量门禁（`quality_check`）**：对解析/OCR 产物执行页级与篇级客观核验（跨法域通用的页码映射、扫描页漏字、原生页丢字、空白页伪影、表格结构 `table_body` 校验、`bbox` 阅读顺序反转，以及 `language == "vi"` 专属的 TCVN3 乱码检测、非越语字符过滤 `non_vietnamese_garbage` 与越南公文落款签章检测），产出确定性路由（`accept_ocr`、`accept_with_warning`、`vision_reparse_pages`、`legacy_font_encoding_recovery`）。未通过必选质量门禁的文档严禁生成成功 `Version`。

## 3. 影响与后果 (Consequences)

- **正面收益**：
  - 依赖树大幅精简，彻底告别 `crawl4ai` 的臃肿依赖与全局缓存污染。
  - 能够以极低成本批量复用 `Juriscraper`、`oldp-ingestor`、`Open States`、`OpenCaseLaw` 和 `World Wide Law` 的上千个成熟站点脚本，同时赋予它们 S3 CAS 原件保全、零网络 `reparse` 和内网 `br-ocr-v1` 深度文档解析能力。
  - 原生复用 [production_document_parse.py](../reference/production_document_parse.py) 与 [office_text_extract.py](../reference/office_text_extract.py)，无需重复造轮子即可直接对接内网 `br-ocr-v1` 生产服务。
- **潜在代价与约束**：
  - 内网 `br-ocr-v1` 属于外部推理计算 I/O，必须在独立提取子进程组（`extract_worker`）中执行，严禁在等待 `/status/{task_uuid}` 轮询期间持有数据库连接事务；且 `wlh reparse` 模式下对已归档 PDF 调用内网 `br-ocr-v1` 属于允许的本地/内网计算解析，不计入外部来源网络请求（`external_requests` 仍为 `0`）。
