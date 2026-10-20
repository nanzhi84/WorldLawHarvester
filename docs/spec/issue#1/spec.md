# WorldLawHarvester 首批架构与分阶段实施计划

> **Status:** Proposal · Issue #1 · Created 2026-10-21
> **Affected Architecture:** [docs/architecture/overview.md](../../architecture/overview.md), [docs/architecture/canonical-legal-model.md](../../architecture/canonical-legal-model.md)

## 1. 背景与目标 (Context & Goals)

### 1.1 背景
在前序系统（SEAL）与 [公开数据源和开源采集项目调研.md](../../reference/公开数据源和开源采集项目调研.md) 的实践中，我们已经验证了一套高可靠的采集与解析工程底座：
- 受控 HTTP / CDP 浏览器获取（`Acquisition`）与 S3 CAS 原始字节强校验；
- 多输入/多目标持久任务 DAG、PostgreSQL advisory lock 与 `attempt_epoch` 并发围栏；
- 零外部来源网络离线重解析（`reparse`）与四重指纹版本去噪；
- 实战打磨的 [office_text_extract.py](../../reference/office_text_extract.py)（Office 容器魔数探测、DOC/DOCX/RTF/XLS 直提、越南语 TCVN3 历史字体逐行恢复）与 [production_document_parse.py](../../reference/production_document_parse.py)（PDF 逐页墨迹比 `ink_ratio` 扫描件判定、内网 `br-ocr-v1` 版面分析服务 `http://192.168.160.67:7555/api/v1` 及页级/篇级客观质量门禁 `quality_check`）。

但旧仓库在演进中遗留了四项需要重构的问题：
1. **残留臃肿的 `crawl4ai` 依赖**：旧仓库仅因 5 行 HTML 转 Markdown 代码保留了 `crawl4ai[pdf]`，引入上百个重型无关依赖及隐式缓存污染。
2. **缺乏对全球开源法律采集生态的轻量桥接能力**：[公开数据源和开源采集项目调研.md](../../reference/公开数据源和开源采集项目调研.md) 评估的五大开源项目（`Juriscraper`、`oldp-ingestor`、`Open States`、`OpenCaseLaw`、`World Wide Law`）拥有现成的站点列表与字段抽取脚本，需要一套极简统一的桥接契约直接复用。
3. **核心表直接写死中文列名，缺乏轻量投影层隔离**：核心数据库表直接用中文列名建表，不利于代码维护与多消费端输出，但**第一版业务只需要采集并输出 [法规案例元数据上传模板](../../reference/法规案例元数据上传模板.xlsx) 的 16 列数据**，不需要过度设计复杂的法学本体论（如法律位阶、法条 AST 树、立改废引用图谱等）。
4. **15 层历史数据库迁移与单层 40+ 文件堆放**：需要拍平为单一纯净的初始 `schema.sql` 与清晰的模块化目录结构。

### 1.2 核心目标
本计划作为 `WorldLawHarvester`（包名/命令/Schema 统一为 `wlh`）的首批蓝图，坚持**“采集层只负责把数据干净抓下来、第一版聚焦 16 列交付字段、保留轻量可插拔投影层”**的原则：
1. **彻底弃用 `crawl4ai`**：以 `httpx`/`aiohttp` + `playwright`（纯 CDP 拦截）+ `lxml`/`selectolax` + 确定性 HTML-to-Markdown 转换器构建轻量纯净的技术栈（见 [ADR-0003](../../adr/0003-multi-adapter-bridges-and-intranet-ocr-stack.md)）。
2. **极简统一的 16 列对标数据结构（`RawRecord`）与五大开源项目桥接**：所有原生 Adapter 与五大开源 Bridge（`JuriscraperBridge`、`OldpProviderBridge`、`OpenStatesBridge`、`OpenCaseLawBridge`、`WorldWideLawBridge`）共享 `CasSessionProxy` 拦截请求归档至 S3 CAS，并统一输出仅包含 **16 列所需字段**的极简结构 `RawRecord`（不做复杂的法条 AST、引证图谱或跨国位阶建模）。
3. **“原生直提优先、OCR 仅作扫描件兜底（本地代管 + 内网可切）”的提取栈**：重构 [office_text_extract.py](../../reference/office_text_extract.py) 与 [production_document_parse.py](../../reference/production_document_parse.py)。HTML/XML、Office 文档及含原生文字层的 PDF 一律直接提取文本（零 OCR 开销）；**仅当 PDF 页面被判定为无文字层的扫描页（`suspected_scan`）时才触发 OCR 兜底**。针对当前无内网环境，默认由本地 `tesseract` 代管扫描页兜底识别（当内网 `br-ocr-v1` 可达时支持切换或自动降级，并将 OCR 结果缓存入 S3 CAS）。
4. **拍平单一纯净数据库基线（`src/wlh/sql/schema.sql`）并保留可插拔投影器（`src/wlh/projections/`）**：核心表（`wlh.document_row`）使用干净的英文标准列名存储 16 列对应数据，通过 `src/wlh/projections/template_16col.py` 投影输出为 16 列中文模板格式（见 [ADR-0001](../../adr/0001-canonical-legal-domain-and-pluggable-projectors.md)）。

---

## 2. 方案设计 (Design & Contracts)

### 2.1 技术栈定案（零 `crawl4ai`）

| 模块层级 | 选定技术栈 | 职责说明 |
| :--- | :--- | :--- |
| **运行时与包管理** | Python `3.12+`、`uv`、`Pydantic v2`、`PyYAML` | 强类型配置与极简数据校验；开源桥接器第三方库通过 `[project.optional-dependencies]` 按需加载 |
| **受控 HTTP 与浏览器获取** | `httpx` / `aiohttp`、`protego`、`playwright`（纯 CDP `Fetch.requestPaused`） | 所有外部请求强制经受控 Transport 写入 S3 CAS 并读回校验 SHA-256 |
| **HTML / XML 解析** | `lxml`、`selectolax`、确定性 DOM->Markdown 转换器 | 取代 `crawl4ai`；完成网页正文清洗、表格还原与官方 XML 正文提取 |
| **Office 与容器提取** | 重构自 [office_text_extract.py](../../reference/office_text_extract.py)：`python-docx`、`openpyxl`、`xlrd`、纯 Python RTF 状态机、隔离 Profile Headless LibreOffice | 魔数识别伪装容器（`.doc` 伪装的 `.xls`/`.xlsx`/`.rtf`/`.zip`）；`.docx`/`.rtf`/`.xls` 直提，`.doc` 优先经 LibreOffice `txt:Text` 提取并执行越南语 TCVN3 恢复，失败时再转 PDF 回退至 OCR |
| **PDF 原生直提与扫描件 OCR 兜底** | 重构自 [production_document_parse.py](../../reference/production_document_parse.py)：**`PyMuPDF` (`fitz`)**、本地 **`tesseract`**（无内网默认代管）、内网 **`br-ocr-v1`**（有内网时可选） | `PyMuPDF` 逐页计算 `native_text_chars` 与 `0.5x` 灰度 `ink_ratio`：原生文字页直接提取文本；**仅对 `suspected_scan` 扫描页触发 OCR 兜底**（当前无内网环境默认使用本地 `tesseract` 逐页代管，内网可达时支持切换 `br-ocr-v1` 并缓存结果至 S3 CAS） |
| **存储与异步队列** | PostgreSQL 16+（`psycopg[binary,pool]` + `procrastinate`）、S3 CAS（`boto3` -> MinIO/S3） | 单一关系型账本 + advisory lock + `attempt_epoch` 围栏 + S3 内容寻址归档 |
| **只读 API 服务** | `Starlette` / `FastAPI` + `uvicorn` | 暴露 `/healthz`、`/results`（默认 `template_16col` 16 列投影视图）与 `/files/{sha256}` |

### 2.2 目标仓库源码目录结构

```text
WorldLawHarvester/
├── pyproject.toml
├── uv.lock
├── compose.yaml                         # 本地 PostgreSQL + MinIO (S3 CAS) 容器编排
├── sources/                             # 绑定地图稳定编号的来源配置 (sources/dd-xxx.yaml)
├── examples/                            # 最小可运行 Source 配置模板
├── src/
│   └── wlh/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py                       # CLI 入口: init / collect / recheck / reparse / worker / snapshot / serve
│       ├── core.py                      # WlhError、确定性 JSON 序列化、SHA-256 摘要与公共原语
│       ├── config.py                    # Source YAML 闭合 Schema 校验
│       ├── db.py                        # PostgreSQL 连接池与事务辅助
│       ├── sql/
│       │   └── schema.sql               # 单一纯净初始数据库 Schema (wlh) + Procrastinate 队列初始化
│       ├── engine/                      # [L1] 通用受控采集、CAS 归档与任务 DAG 运行时
│       │   ├── objects.py               # S3 CAS 存取与读回 SHA-256 强校验
│       │   ├── scope.py                 # 公开地址、域名/路径范围与安全守卫
│       │   ├── robots.py                # 受控 robots.txt 获取与策略判定
│       │   ├── transport.py             # 受控 HTTP 传输、重试/重定向事实链与请求预算
│       │   ├── browser.py               # CDP Fetch.requestPaused 逐跳拦截与 rendered_dom 获取
│       │   ├── browser_runtime.py       # 隔离 Chromium 生命周期管理
│       │   ├── runs.py                  # Run 创建、advisory lock、attempt_epoch 围栏与子进程监管
│       │   ├── runtime.py               # 任务/目标 DAG 循环、RawRecord 单轨驱动、离线 reparse 阻断与水位 checkpoint
│       │   ├── runtime_registration.py  # 请求任务与文档目标同事务持久登记与 amend
│       │   ├── runtime_process.py       # 独立进程组执行器与父进程存活监控
│       │   ├── runtime_report.py        # receipt.v1 运行报告、7 类失败统计与 coverage
│       │   ├── recheck.py               # 到期 Resource 重检规划
│       │   ├── reparse.py               # 已归档 CAS 输入冻结与零来源网络重解析规划
│       │   ├── queue.py                 # Procrastinate 异步队列、心跳恢复与补入队
│       │   └── progress.py              # TTY 进度面板与终端报告
│       ├── extraction/                  # [L2a] 异构法律文档提取、内网 br-ocr-v1 与客观质量门禁
│       │   ├── manifest.py              # 解析器语义配置摘要与 parser_fingerprint 计算
│       │   ├── extract_worker.py        # 受控子进程组提取入口与主机级资源槽位控制 (slots.py)
│       │   ├── office.py                # 重构自 office_text_extract.py: 魔数探测、DOCX/RTF/XLS/ZIP 直提、DOC txt:Text+TCVN3 优先与 PDF 回退
│       │   ├── pdf_scan.py              # 重构自 production_document_parse.py: 基于 PyMuPDF (fitz) 逐页 native_text_chars + 0.5x 灰度 ink_ratio 诊断
│       │   ├── ocr_br.py                # 内网 br-ocr-v1 客户端 (/file_parse -> /status -> /result) 及 S3 CAS 派生 OCR JSON 缓存读写
│       │   ├── ocr_tesseract.py         # 本地受控进程组 tesseract 备选引擎
│       │   ├── quality.py               # 重构自 production_document_parse.py: 按语种参数化的客观质量门禁 (quality_check) 与路由
│       │   ├── markdown.py              # 基于 lxml 的确定性 HTML-to-Markdown 与纯文本归一化 (零 crawl4ai)
│       │   └── vi_tcvn3.py              # 重构自 office_text_extract.py: 越南语 TCVN3 逐行识别恢复与标题层级还原
│       ├── domain/                      # [L2b] 极简 16 列核心记录、版本提交与全局快照
│       │   ├── document.py              # RawRecord -> wlh.inputs.v1 & wlh.document.v1 构建、校验与四重指纹
│       │   ├── resources.py             # Resource 锁、幂等重放核验、Observation/Version/document_row 提交
│       │   └── snapshots.py             # 全局 Snapshot 创建、覆盖率计算、CAS 闭包校验与原子发布切换
│       ├── adapters/                    # [L2c] 原生站点适配器与五大开源采集生态桥接层
│       │   ├── __init__.py              # Adapter 协议、极简统一结构 RawRecord / RawUnitSource 与 CasSessionProxy
│       │   ├── web.py                   # 通用公开网页与附件发现适配器 -> RawRecord
│       │   ├── vietnam_law.py           # 越南政府公报/法典适配器 -> RawRecord
│       │   ├── federal_register.py      # 美国联邦公报 API/XML/PDF 适配器 -> RawRecord
│       │   ├── structured_xml.py        # 官方立法 XML 适配器 (AKN / USLM / 台湾法规 XML / 日本 e-Gov) -> RawRecord
│       │   └── bridges/                 # 五大开源法律爬虫脚本桥接器 (共享 CasSessionProxy，统一输出 RawRecord)
│       │       ├── __init__.py
│       │       ├── juriscraper_bridge.py   # [1] Juriscraper (美国法院) -> RawRecord
│       │       ├── oldp_bridge.py          # [2] oldp-ingestor (德国/欧盟) -> RawRecord
│       │       ├── openstates_bridge.py    # [3] Open States (美国各州法案) -> RawRecord
│       │       ├── opencaselaw_bridge.py   # [4] OpenCaseLaw (瑞士法院与 Fedlex) -> RawRecord
│       │       └── worldwidelaw_bridge.py  # [5] World Wide Law (全球公报如 VN/CongBao) -> RawRecord
│       ├── projections/                 # [L3a] 可插拔下游投影器 (保留投影设计)
│       │   ├── __init__.py              # Projector 注册表与统一协议
│       │   ├── canonical.py             # 英文标准键名直出视图
│       │   └── template_16col.py        # 对齐《法规案例元数据上传模板.xlsx》的 16 列中文交付投影
│       └── api/                         # [L3b] 只读查询与文件直取服务
│           ├── server.py                # ASGI/WSGI 只读服务: /healthz, /results, /files/{sha256}
│           ├── security.py              # Bearer 令牌摘要鉴权、Source 授权边界与游标签名
│           └── queries.py               # 基于已发布 Snapshot 的分页查询与 CAS 文件闭包读取
└── tests/                               # 核心数据流与黑盒契约测试
```

### 2.3 极简统一的 16 列候选数据结构（`RawRecord`）与五大开源桥接契约

**第一版不做任何多余的法学本体设计**。所有原生 Adapter 与五大开源 Bridge 只负责把站点上对应 **16 列模板**的字段抓下来，填入统一的 **`RawRecord`** 中：

#### 2.3.1 统一中间数据结构：`RawRecord`（直接对齐 16 列需求）

```python
class RawUnitSource(BaseModel):
    """单个文件单元（主文档、分卷或同源附件）的原始字节或待下载请求"""
    title: str | None = None                    # 对应【1. 资料名称】（为空时继承外层 RawRecord.title）
    volume_order: int | None = None             # 对应【6. 文件顺序】（仅多卷本为 1, 2...，普通文件为 None）
    request: RequestSpec | None = None          # 待下载归档的原件链接（对应【5. 原始文件】-> 提取【2. 解析后文件】）
    inline_acquisition_id: str | None = None    # 若仅有网页正文无独立附件，指向已归档网页 Acquisition ID
    semantic_locator: str | None = None         # 网页正文局部选择器（如 XPath/CSS），用于算抗噪 slice_hash
    inline_bytes: bytes | None = None           # 网页内联正文切片字节
    media_type: str | None = None
    original_filename: str | None = None

class RawRecord(BaseModel):
    """所有原生 Adapter 与五大开源 Bridge 的唯一统一输出结构（严格聚焦 16 列核心字段）"""
    external_key: str                           # 站点唯一标识 -> 自动生成 64位【15. 来源记录号】
    title: str                                  # 对应【1. 资料名称】
    doc_type: str | None = None                 # 对应【3. 资料类型】（为空时继承 Source YAML 默认值）
    document_number: str | None = None          # 对应【7. 文号/案号】
    issuer: str | None = None                   # 对应【8. 发布机构】（直接存字符串，多机构以 "; " 拼接）
    published_at: date | None = None            # 对应【9. 发布日期】
    effective_at: date | None = None            # 对应【10. 生效日期】
    expired_at: date | None = None              # 对应【11. 失效日期】
    status: str | None = None                   # 对应【12. 状态】（如 现行有效/已废止/已失效 等，原样或映射存储）
    source_name: str | None = None              # 对应【13. 来源名称】（为空时继承 Source YAML 默认值）
    detail_url: str | None = None               # 对应【14. 来源详情页】
    units: list[RawUnitSource]                  # 1..N 个文件单元 -> 产出【2. 解析后文件】【5. 原始文件】【6. 文件顺序】
```
> 注：第 4 列【采集时间 (`acquired_at`)】与第 16 列【寻址哈希 (`original_hash` / `markdown_hash`)】由引擎在归档与提取完成时自动生成，无需 Adapter 手填。

#### 2.3.2 统一受控会话代理：`CasSessionProxy`

- 在 `collect` / `recheck` 模式下，向开源脚本注入 `CasSessionProxy`：脚本调用 `.get()` / `.post()` 时直接通过 `wlh` 受控 `Transport` 同步完成“限速获取 -> 写入 S3 CAS -> 读回校验 SHA-256 -> 短事务提交 `archived Acquisition` -> 返回响应字节”；崩溃恢复或 `reparse` 模式下 `O(1)` 从 CAS 直接命中回放。
- 五大开源项目（`juriscraper_bridge.py`、`oldp_bridge.py`、`openstates_bridge.py`、`opencaselaw_bridge.py`、`worldwidelaw_bridge.py`）各自只需 ~30 行代码将上游字段（标题、案号/文号、法院/机关名称、发布日、详情链接、PDF/DOCX 链接）填入上述 `RawRecord` 即可。

### 2.4 原生直提优先与扫描件 OCR 兜底合同（本地代管 + 内网可切）

`src/wlh/extraction/` 整合 [office_text_extract.py](../../reference/office_text_extract.py) 与 [production_document_parse.py](../../reference/production_document_parse.py)，严格遵循**“直提为主、OCR 仅作扫描页最后兜底”**的三级流水线：

1. **第一级：Office 直提与 `.doc` TCVN3 优先恢复（`office.py` + `vi_tcvn3.py`，零 OCR）**：
   - 按魔数探测真实格式（识别伪装为 `.doc` 的 `.xls`/`.xlsx`/`.rtf` 或 `.zip`）。
   - `.docx`、`.rtf`、`.xls`/`.xlsx` 直接结构化提取；二进制 `.doc` 优先经 Headless LibreOffice `--convert-to txt:Text` 提取并执行 `normalize_detected_tcvn3` 逐行恢复越南语 TCVN3 编码；仅当直提无有效文本（如 `.doc` 内嵌纯扫描图）时才转 PDF 进入下一级。
2. **第二级：基于 `PyMuPDF` (`fitz`) 的 PDF 原生文本直提与逐页扫描判定（`pdf_scan.py`）**：
   - 逐页计算 `native_text_chars`、`embedded_images` 与 `0.5x` 灰度 `ink_ratio`（阈值 `< 210`）。
   - **原生文字页直接采用 `PyMuPDF` 提取文本（并支持 TCVN3 恢复），完全不走 OCR**。
   - 仅当某页满足 `native_text_chars < 30` 且 `ink_ratio > 0.003` 时，将该页标记为 `suspected_scan`，交由第三级 OCR 兜底识别后再与原生文字页按页码拼接。
3. **第三级：OCR 引擎自动主备兜底——先尝试内网 `br-ocr-v1`，不可达时自动切本机 `tesseract`（`ocr_br.py` -> `ocr_tesseract.py`）**：
   - **不搞手动二选一路由，固定走“内网主力 -> 本机兜底”自动降级链**：
     1. **先尝试内网 `br-ocr-v1`**（`http://192.168.160.67:7555/api/v1`）：先命中 S3 CAS 缓存；未命中时以短连接超时（如 `connect_timeout = 3s`）请求 `/file_parse` -> `/status/{uuid}` -> `/result/{uuid}`，成功则将结果缓存入 S3 CAS。
     2. **失败/无内网时自动兜底切本机 `tesseract`**：当处于无内网环境（连接超时/不可达）或内网服务异常时，无需修改 YAML 配置，自动降级调用本机受控 `tesseract` 进程对扫描页进行识别代管，确保无内网环境下照常跑通全流程。
4. **按语种参数化的客观质量门禁（`quality.py`）**：
   - 对最终提取或 OCR 产物执行客观质量检查：跨语种通用的扫描页缺字（`scan_page_with_little_ocr_text`）、空白伪影、Unicode 替换字符（`replacement_character`），以及 `language == "vi"` 专属的越南语 TCVN3 乱码与 `non_vietnamese_garbage` 检查。

### 2.5 Source 配置合同（`sources/dd-xxx.yaml`）

```yaml
id: dd-492
name: 越南政府规范性法律文件库 (VBQPPL Chính phủ)
adapter: vietnam_law
adapter_options:
  max_pages: 5
entry_urls:
  - https://vanban.chinhphu.vn/
scope: 越南政府门户公开法律法规、政令与总理决定及其官方 DOC/PDF 附件。
archival_permitted: true
metadata:
  doc_type: 法律法规                     # 直接对应 16 列【资料类型】默认值
  language: vi
  source_name: Cổng Thông tin điện tử Chính phủ
fetch:
  mode: http                           # http 或 rendered_dom
  concurrency: 2
  delay_ms: 500
  page_timeout_ms: 60000
  max_depth: 1
  respect_robots: true
budget:
  requests: 200
  documents: 100
  seconds: 600
  response_bytes: 134217728
documents:
  office_text_policy: prefer
ocr:
  server_url: http://192.168.160.67:7555/api/v1
  model: br-ocr-v1
  connect_timeout_seconds: 3.0         # 无内网时 3 秒快速判定不可达，立即触发本机 tesseract 兜底
  timeout_seconds: 600
  poll_seconds: 1.0
  fallback_local: true                 # 内网不可达或失败时，自动降级切到本机 tesseract 代管
  lang: vie                            # 本机 tesseract 兜底语言包 (如 vie / chi_sim / eng)
  cache_in_cas: true                   # 将 OCR 结果缓存入 S3 CAS，加速离线 reparse
parser:
  quality_accept_routes:
    - accept_ocr
    - accept_with_warning
```

### 2.6 第一版适配网址实验样本矩阵（第一梯队 & 第二梯队）

| 梯队 | 地图编号 / 标识 | 来源名称与入口地址 | 适配器 / 桥接器 | 覆盖输入形态 | 核心验证目标与期望 16 列产出 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **第一梯队**<br>*(Phase 4–5 必选首发)* | **[dd-499](../../reference/尽职调查核验入口地图.md#dd-499)** | **美国联邦公报官方 API**<br>`https://www.federalregister.gov/api/v1/documents.json` | `federal_register.py`<br>*(原生 Adapter)* | REST JSON 列表 + XML + 官方原生文字 PDF | **打头阵跑通全链路**：免 Key、零反爬。验证 API 分页发现 $\rightarrow$ 派生 PDF 请求归档 S3 CAS $\rightarrow$ `PyMuPDF` 原生文本直提（零 OCR）$\rightarrow$ 16 列字段（标题、文号、发布机构、发布/生效日期、PDF 原件与 `.md`）完整落库与投影 |
| **第一梯队**<br>*(Phase 4–5 必选首发)* | **[dd-017](../../reference/尽职调查核验入口地图.md#dd-017)** | **最高人民法院「指导案例」**<br>`https://www.court.gov.cn/shenpan/gengduo/77.html` | `web.py`<br>*(原生 Adapter)* | 静态 HTML 列表页 + 详情页内联正文 | **验证纯网页无附件采集**：验证移除 `crawl4ai` 后的 `lxml` 确定性 HTML-to-Markdown 转换、无独立原件（`原始文件 = null`，仅生成 `.md`）时的 16 列交付、局部 `slice_hash` 抗外围噪音及中文环境 `quality_check` |
| **第一梯队**<br>*(Phase 4–5 必选首发)* | **[dd-492](../../reference/尽职调查核验入口地图.md#dd-492)**<br>**[dd-493](../../reference/尽职调查核验入口地图.md#dd-493)** | **越南政府文献门户 & 越南电子公报**<br>`https://vanban.chinhphu.vn/`<br>`https://congbao.chinhphu.vn/` | `vietnam_law.py`<br>*(原生 Adapter)* | 政务详情页 HTML + 历史 `.doc`/`.docx`/`.rtf` + 红章扫描件 PDF | **终极实战大考**：验证越语政务详情页题录提取（文号 `Số ký hiệu`、机关、发布/生效日）、Office 魔数探测与 `.doc -> txt:Text -> TCVN3` 历史字体恢复、以及扫描件 PDF 触发 **OCR 主备自动兜底（先试内网 `br-ocr-v1` $\rightarrow$ 3s 不通自动切本机 `tesseract`）** |
| **第二梯队**<br>*(Phase 6 生态扩展)* | **[dd-493](../../reference/尽职调查核验入口地图.md#dd-493)**<br>*(WWL 样本)* | **World Wide Law: `VN/CongBao`**<br>越南电子公报开源采集脚本 | `worldwidelaw_bridge.py`<br>*(开源 Bridge)* | 生成器 `fetch_updates` + DOCX/PDF 附件 | 验证 `CasSessionProxy` 拦截外部生成器请求归档 S3 CAS，将上游字典映射为统一 `RawRecord`，并与原生 `vietnam_law.py` 采集结果交叉对账 |
| **第二梯队**<br>*(Phase 6 生态扩展)* | **`us-ca1`**<br>*(Juriscraper 样本)* | **Juriscraper: 美国联邦第一巡回上诉法院 (`ca1`)** | `juriscraper_bridge.py`<br>*(开源 Bridge)* | 法院公开列表 HTML + 判决书 PDF | 验证拦截 `OpinionSite._download` 走 S3 CAS、复用 `_process_html()` 抽取案名/案号/裁判日/法官映射为 `RawRecord`（`资料类型 = 案例`）并完成 PDF 提取 |
| **第二梯队**<br>*(Phase 6 生态扩展)* | **`de-bgh` / `ch-bger`**<br>*(oldp / OpenCaseLaw / OpenStates)* | **德国联邦法院 (`oldp`) / 瑞士联邦法院 (`OpenCaseLaw`) / 美国州法案 (`OpenStates`)** | `oldp_bridge.py`<br>`opencaselaw_bridge.py`<br>`openstates_bridge.py` | 法院/议会列表 + HTML/PDF/多版本法案附件 | 验证多开源框架统一坍缩为 `RawRecord`（含多卷本 `volume_order` 映射），无缝复用同一套 CAS 归档与 16 列导出管道 |

---

## 3. 分阶段实施计划与逐阶段验收标准 (Phased Plan & Acceptance Criteria)

后续编码按以下 **6 个连续阶段**推进（Phase 1–5 先用第一梯队原生 Adapter 跑通从采集到 16 列 API 导出的完整交钥匙闭环，Phase 6 再横向扩展五大开源项目 Bridge）：

### Phase 1：存储底座、极简 16 列 Schema 与无 `crawl4ai` 依赖配置
- **实施任务 Checklist**：
  - [ ] 编写精简的 `pyproject.toml`（移除 `crawl4ai`；核心依赖仅保留 `psycopg[binary,pool]`、`boto3`、`httpx`、`aiohttp`、`protego`、`playwright`、`lxml`、`selectolax`、`pymupdf`、`python-docx`、`openpyxl`、`xlrd`、`procrastinate`、`pydantic`、`pyyaml`、`starlette`/`fastapi`、`uvicorn`、`rich`；开源桥接依赖放入 `[project.optional-dependencies]`）并生成 `uv.lock`。
  - [ ] 编写本地 `compose.yaml`（PostgreSQL 16 + MinIO S3 CAS）。
  - [ ] 实现 `src/wlh/core.py`（`WlhError`、确定性 JSON 序列化、SHA-256 摘要、`source_record_hash` 算法）、`src/wlh/config.py`（Source YAML 闭合校验）与 `src/wlh/db.py`（连接池与短事务辅助）。
  - [ ] 实现 `src/wlh/engine/objects.py`（S3 CAS 写入后立即 `get()` 读回校验 SHA-256；严禁直接读写本地 `minio/` 目录）。
  - [ ] 编写单一纯净基线 `src/wlh/sql/schema.sql`（建立 `wlh` Schema 下的 `source`、`frontier`、`run`、`acquisition`、`task`、`task_input`、`resource`、`observation`、`observation_evidence`、`delivery_conflict`、`version`、直接对标 16 列的 `document_row`、`snapshot*`、`publication` 表及封存不可变触发器）与 `wlh init` 命令。
- **Phase 1 验收标准**：
  - `uv run wlh init` 在全新 PostgreSQL 库上一次性建表成功且幂等；
  - S3 CAS 写入字节流后自动读回校验 SHA-256 通过，篡改字节时立即抛出校验异常。

### Phase 2：受控获取（HTTP / CDP Browser）、`CasSessionProxy` 与任务 DAG 运行时
- **实施任务 Checklist**：
  - [ ] 实现 `src/wlh/engine/scope.py` 与 `robots.py`（域名/路径边界守卫与受控 `robots.txt` 归档判定）。
  - [ ] 实现 `src/wlh/engine/transport.py`（受控 HTTP 获取、重定向逐跳记录 `parent_acquisition_id`、请求预算控制、写入 S3 CAS 并提交 `state = 'archived'` 的 `Acquisition`）。
  - [ ] 实现 `src/wlh/engine/browser.py` 与 `browser_runtime.py`（基于 `playwright` CDP `Fetch.requestPaused` 逐跳拦截归档与 `rendered_dom` 获取）。
  - [ ] 实现 `src/wlh/adapters/__init__.py` 中的统一候选结构 `RawRecord` / `RawUnitSource` 与 `CasSessionProxy`（`collect` 模式同步走受控 `Transport` 归档，`reparse` 模式锁死 `external_requests = 0` 纯 CAS 回放）。
  - [ ] 实现 `runs.py`、`runtime.py`、`runtime_registration.py`、`runtime_process.py`、`runtime_report.py`、`recheck.py`、`reparse.py`、`queue.py`（PostgreSQL advisory lock、`attempt_epoch` 围栏、任务 DAG 状态机流转）。
- **Phase 2 验收标准**：
  - 模拟外部请求（含 302 重定向跳）全部落盘 S3 CAS 并在 `wlh.acquisition` 表生成完整证据链；
  - 在 `mode = 'reparse'` 下调用 `Transport` 或 `CasSessionProxy` 发起未归档外部请求时被硬性阻断（`external_requests == 0`）；
  - 并发启动同一 Source 的两个 Run 时，第二个 Run 被 advisory lock 正确互斥拒绝，旧 `attempt_epoch` 的过期写入被围栏拦截。

### Phase 3：原生直提与 OCR 主备自动兜底（内网 `br-ocr-v1` -> 本机 `tesseract`）
- **实施任务 Checklist**：
  - [ ] 实现 `src/wlh/extraction/markdown.py`（基于 `lxml` 的确定性 HTML-to-Markdown 转换与表格还原，彻底替代 `crawl4ai`）。
  - [ ] 重构合入 [office_text_extract.py](../../reference/office_text_extract.py) 至 `src/wlh/extraction/office.py` 与 `vi_tcvn3.py`：实现容器魔数探测（识别伪装为 `.doc` 的 `.xls`/`.xlsx`/`.rtf`/`.zip`）、`.docx`/`.rtf`/`.xls` 直提、二进制 `.doc` 优先经 Headless LibreOffice `txt:Text` 提取 + 越南语 TCVN3 逐行恢复，无有效文本时转 PDF 回退。
  - [ ] 重构合入 [production_document_parse.py](../../reference/production_document_parse.py) 至 `pdf_scan.py`、`ocr_br.py`、`ocr_tesseract.py` 与 `quality.py`：
    - `pdf_scan.py`：基于 `PyMuPDF` (`fitz`) 逐页计算 `native_text_chars` 与 `0.5x` 灰度 `ink_ratio`；原生文字页直接抽文本，仅将 `suspected_scan` 页送入 OCR；
    - `ocr_br.py` & `ocr_tesseract.py`：**先尝试内网 `br-ocr-v1`**（设 `connect_timeout = 3.0s`，成功则缓存 JSON 至 S3 CAS），**当无内网环境连接不可达或超时失败时，自动无缝 fallback 切到本机 `tesseract` 代管识别**；
    - `quality.py`：按 `language` 参数化执行通用门禁与越语专属门禁（防止中文/英文文档被 `non_vietnamese_garbage` 误杀）。
- **Phase 3 验收标准**：
  - 给定测试样本（HTML、伪装后缀的 `.xls`/`.rtf`、含 TCVN3 字体的越语 `.doc`/`.rtf`、纯文字 PDF、扫描件 PDF），在**当前无内网环境**下：
    1. 文字版 PDF 与 Office 文档毫秒级直提成功（零 OCR 调用），TCVN3 乱码行 100% 恢复为正确越南语 Unicode 且保留同文 Unicode 抬头；
    2. 扫描件 PDF 在 3 秒内探测到内网 `br-ocr-v1` 不可达，自动降级调用本机 `tesseract` 完成识别并通过 `quality_check`。

### Phase 4：16 列核心记录落库（`wlh.document.v1`）与第一梯队原生 Adapter
- **实施任务 Checklist**：
  - [ ] 实现 `src/wlh/domain/document.py` 与 `resources.py`：将 `RawRecord` 与提取产物封装为 `wlh.inputs.v1` 和 `wlh.document.v1` 存入 S3 CAS，计算四重确定性指纹（`input_fingerprint`、`parser_fingerprint`、`version_fingerprint`、`content_hash`），在短事务内提交 `Observation`、`ObservationEvidence`、`Version` 与 16 列 `document_row`。
  - [ ] 实现第一梯队 3 个原生 Adapter 及对应 `sources/dd-xxx.yaml`：
    1. `src/wlh/adapters/federal_register.py`（[dd-499](../../reference/尽职调查核验入口地图.md#dd-499) 美国联邦公报 API + PDF）；
    2. `src/wlh/adapters/web.py`（[dd-017](../../reference/尽职调查核验入口地图.md#dd-017) 最高人民法院指导案例 HTML）；
    3. `src/wlh/adapters/vietnam_law.py`（[dd-492](../../reference/尽职调查核验入口地图.md#dd-492) 越南政府文献门户 & [dd-493](../../reference/尽职调查核验入口地图.md#dd-493) 越南电子公报）。
- **Phase 4 验收标准**：
  - 依次对 `dd-499`、`dd-017`、`dd-492` 执行有界 `wlh collect`：数据库 `wlh.document_row` 准确生成与 16 列一一对应的记录，原件与 `.md` 全部在 S3 CAS 校验通过；
  - 紧接着执行 `wlh recheck`（原件未变）：仅新增 `Observation` 打卡记录，不制造重复 `Version`；
  - 执行 `wlh reparse`：`receipt.json` 中 `external_requests == 0`，纯离线完成重算。

### Phase 5：全局快照、可插拔投影器（`template_16col` & `canonical`）与只读 API 闭环
- **实施任务 Checklist**：
  - [ ] 实现 `src/wlh/domain/snapshots.py`（`wlh snapshot create / list / inspect / publish`，封存前全量读回核验 S3 CAS 引用闭包 SHA-256）。
  - [ ] 实现 `src/wlh/projections/`：`template_16col.py`（将 `document_row` 英文标准列确定性映射为 [法规案例元数据上传模板](../../reference/法规案例元数据上传模板.xlsx) 的 16 个中文列名）与 `canonical.py`（英文列名直出）。
  - [ ] 实现 `src/wlh/api/`（`GET /healthz`、`GET /results?projection=template_16col`、`GET /files/{sha256}`，含 Bearer 鉴权与游标分页）。
- **Phase 5 验收标准（v1 交钥匙里程碑）**：
  - 针对第一梯队三个真实来源走完 `collect -> reparse -> snapshot create -> snapshot publish -> serve` 全流程；
  - 请求 `GET /results` 返回的 JSON 行严格包含 16 列模板要求的全部中文键名（`资料名称`、`解析后文件`、`资料类型`、`采集时间`、`原始文件`、`文件顺序`、`文号/案号`、`发布机构`、`发布日期`、`生效日期`、`失效日期`、`状态`、`来源名称`、`来源详情页`、`来源记录号` 及下载哈希），且通过 `GET /files/{sha256}` 下载的原件与 `.md` 哈希 100% 匹配。

### Phase 6：官方立法 XML 适配器与五大开源生态 Bridge 扩展
- **实施任务 Checklist**：
  - [ ] 实现 `src/wlh/adapters/structured_xml.py`（支持 AKN 3.0 / USLM / 台湾法规 XML / 日本 e-Gov XML 转为 `RawRecord`）。
  - [ ] 在 `src/wlh/adapters/bridges/` 下实现五大开源项目轻量桥接器（统一通过 `CasSessionProxy` 归档并输出 `RawRecord`）：
    - [ ] `worldwidelaw_bridge.py`（以 `VN/CongBao` 对接 `dd-493` 验证）；
    - [ ] `juriscraper_bridge.py`（以美国联邦上诉法院 `ca1` 样本验证）；
    - [ ] `oldp_bridge.py`（德国/欧盟判例与法规样本验证）；
    - [ ] `opencaselaw_bridge.py`（瑞士联邦/州法院 `Decision` 样本验证）；
    - [ ] `openstates_bridge.py`（美国州议会 `Bill` 多版本/附件样本验证）。
- **Phase 6 验收标准**：
  - 第二梯队样本通过各自 Bridge 采集后，无需修改任何下游提取、入库或投影代码，直接通过 `GET /results?projection=template_16col` 输出合规的 16 列结果。

---

## 4. 总体验收纪律 (Verification Discipline)

根据 `AGENTS.md` 的真实执行验收规范，严禁编写依赖 Mock 或拼凑覆盖率的无效单元测试，所有阶段均通过真实运行验收：

1. **静态与架构契约巡检**：
   - 每次文档或代码变更后运行 `harness doctor`，确保目录结构、Mermaid 架构图、ADR 状态、参考索引与本地链接 0 错误。
2. **隔离沙盒真实运行验收**：
   - 使用 `harness run <phase-task> -- uv run wlh ...` 在 `work/` 与 `artifacts/` 中针对第一梯队（`dd-499`、`dd-017`、`dd-492`/`dd-493`）与第二梯队样本执行真实采集、离线 `reparse`、快照发布与 API 导出校验，以 `artifacts/` 下的实际 `receipt.json` 和 16 列输出产物作为唯一验收依据。
