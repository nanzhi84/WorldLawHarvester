<p align="center">
  <h1 align="center">WorldLawHarvester (<code>wlh</code>)</h1>
</p>

<p align="center"><strong>跨法域公开法律法规、司法判例与政府公报受控采集、归档与结构化引擎</strong></p>

<p align="center">
  <code>WorldLawHarvester</code> 面向全球多法域构建可审计的法律文献采集与归档底座。系统坚持“先 S3 CAS 原件归档校验、后离线提取与质量门禁”，原生桥接五大开源法律采集生态（<code>Juriscraper</code>、<code>oldp-ingestor</code>、<code>Open States</code>、<code>OpenCaseLaw</code>、<code>World Wide Law</code>）并对接内网 <code>br-ocr-v1</code> 版面 OCR 服务，第一版极简聚焦 16 列核心采集字段（<code>wlh.document.v1</code>），通过保留的可插拔投影器按需输出下游视图。
</p>

## Core ideas

- **受控获取与五大开源生态桥接（Adapter Bridges）**：彻底弃用臃肿的 `crawl4ai`，以轻量受控 HTTP / CDP 浏览器底座先将所有外部原始字节归档至私有 S3 CAS 并校验 SHA-256；原生提供五大桥接器无缝复用 `Juriscraper`、`oldp-ingestor`、`Open States`、`OpenCaseLaw` 与 `World Wide Law` 的站点解析逻辑，与原生 Adapter 统一汇聚为唯一的 16 列对标候选结构 `RawRecord`，支持零外部网络离线重解析（`wlh reparse`）。
- **聚焦 16 列核心采集模型 + 可插拔投影器**：采集层只负责把数据干净抓下来，核心表（`wlh.document_row`）与只读接口（`GET /results?projection=canonical`）统一使用英文规范键值对，拒绝过度设计；同时在 [examples/field_mapping.yaml](examples/field_mapping.yaml) 中维护与下游 16 列中文模板的中英字段映射表。
- **内网 `br-ocr-v1` 与 Office 深度提取管道**：复用并内化 [office_text_extract.py](docs/reference/office_text_extract.py) 与 [production_document_parse.py](docs/reference/production_document_parse.py)，内置容器魔数探测、Office 结构化直提、越南语 TCVN3 历史字体逐行恢复、基于 `PyMuPDF` (`fitz`) 的 PDF 逐页墨迹扫描件判定、内网 `br-ocr-v1` 版面 OCR（带 S3 CAS 结果缓存，兼顾 `tesseract` 备选）与多语种参数化的客观质量门禁（`quality_check`）。

## Get started

### 1. 启动基础存储服务与安装依赖

本地开发依赖 PostgreSQL（默认端口 `15432`）与 S3 兼容对象存储 MinIO（默认端口 `19000`）：

```bash
# 启动本地 PostgreSQL 与 MinIO 容器（见 compose.yaml）
docker compose up -d

# 同步 Python 虚拟环境与依赖（Python >= 3.12）
uv sync
```

默认连接配置可通过环境变量覆盖（未设置时自动回退至本地容器默认值）：

```bash
export WLH_DATABASE_URL="postgresql://wlh:wlh@127.0.0.1:15432/wlh"
export WLH_S3_ENDPOINT_URL="http://127.0.0.1:19000"
export WLH_S3_BUCKET="wlh-cas"
export WLH_S3_ACCESS_KEY_ID="minioadmin"
export WLH_S3_SECRET_ACCESS_KEY="minioadmin"
```

### 2. 初始化数据库 Schema 与对象存储桶

```bash
uv run wlh init
```

### 3. 运行受控采集、增量复查与零网络离线重解析

以 `sources/` 下已登记的来源配置（如 [sources/dd-499.yaml](sources/dd-499.yaml) 美国联邦公报 API 或 [sources/dd-017.yaml](sources/dd-017.yaml) 最高人民法院指导性案例）为例：

```bash
# 在线受控采集：先将原始响应与附件归档至 S3 CAS，再提取 Markdown 并入库
uv run wlh collect sources/dd-499.yaml

# 在线复查：检验已发现资源是否发生变更
uv run wlh recheck sources/dd-499.yaml

# 离线重解析：冻结已归档 CAS 原件，在零外部网络请求（external_requests = 0）下重算版本
uv run wlh reparse sources/dd-499.yaml

# 也可将任务入队由后台 Worker 异步消费
uv run wlh collect sources/dd-499.yaml --enqueue
uv run wlh worker --once
```

### 4. 封存发布全局快照与启动只读交付 API

```bash
# 创建不可变全局快照（自动核验所含全部 CAS 引用 SHA-256 闭包）
uv run wlh snapshot create

# 查看快照列表并将指定快照切换为当前对外发布版本
uv run wlh snapshot list
uv run wlh snapshot publish <snapshot_id>

# 启动只读查询与文件下载服务（统一提供 canonical 英文规范键值对投影）
uv run wlh serve --config examples/api.yaml --host 127.0.0.1 --port 8080
```

### 5. 静态检查、黑盒契约测试与规范巡检

```bash
# 代码风格与静态类型检查
uv run ruff check src tests
uv run pyright

# 运行全真 PostgreSQL + S3 CAS 黑盒契约测试套件
uv run pytest -v

# 运行仓库结构、架构图约定与文档死链巡检
harness doctor
```

## Learn more

- **协作规范与领域红线**：[AGENTS.md](AGENTS.md) · [PROJECTS.md](PROJECTS.md)
- **文档导航与全局架构**：[文档维护规范](docs/README.md) · [全局架构现状](docs/architecture/overview.md) · [通用法律模型与投影架构](docs/architecture/canonical-legal-model.md)
- **首批实施计划与架构决策**：[Issue #1 首批架构与分阶段实施计划](docs/spec/issue#1/spec.md) · [ADR-0001](docs/adr/0001-canonical-legal-domain-and-pluggable-projectors.md) · [ADR-0002](docs/adr/0002-controlled-cas-acquisition-and-offline-reparse.md) · [ADR-0003](docs/adr/0003-multi-adapter-bridges-and-intranet-ocr-stack.md)
- **源站适配台账与外部参考**：[多法域资源适配与推进台账](docs/runbooks/source-adaptation-matrix.md) · [参考资料索引](docs/reference/INDEX.md) · [CHANGELOG.md](CHANGELOG.md)
