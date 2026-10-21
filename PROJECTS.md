# PROJECTS.md — WorldLawHarvester (`wlh`) 项目特异规范与领域红线

## 1. 项目定位与命名约定
- **项目名称**：`WorldLawHarvester`（跨法域公开法律法规、司法判例与政府公报受控采集、归档与结构化引擎）。
- **统一标识符**：
  - Python 顶层包名：`wlh`（源码位于 `src/wlh/`）
  - CLI 命令名：`wlh`（`wlh init`、`wlh collect`、`wlh recheck`、`wlh reparse`、`wlh worker`、`wlh snapshot`、`wlh serve`）
  - PostgreSQL Schema：`wlh`

## 2. 来源身份与稳定编号（`dd-xxx`）
- [多法域资源适配与推进台账（CSV）](docs/runbooks/source-adaptation-matrix.csv) 是所有采集来源身份、国家/法域分类、源级元数据与适配状态的**唯一事实源（Single Source of Truth）**；同目录下的 [多法域资源适配与推进台账（Markdown）](docs/runbooks/source-adaptation-matrix.md) 由 CSV **单向自动生成**（`CSV -> MD`，严禁直接手改 Markdown 表格行，否则会被同步覆盖）。原 [尽职调查核验入口地图](docs/reference/尽职调查核验入口地图.md) 与 [公开数据源和开源采集项目调研](docs/reference/公开数据源和开源采集项目调研.md) 作为初始静态调研基线封存。
- 新增或变更来源必须统一在 [`docs/runbooks/source-adaptation-matrix.csv`](docs/runbooks/source-adaptation-matrix.csv) 中登记稳定 `dd-xxx` ID（从当前最大序号递增，严禁重排、回填或复用），由文件改动 Hook（或 `PYTHONPATH=src python3 -m wlh.matrix`）单向联动更新 [`docs/runbooks/source-adaptation-matrix.md`](docs/runbooks/source-adaptation-matrix.md) 的章节与 `<a id="dd-xxx"></a>` 锚点；Source 配置统一命名为 `sources/dd-xxx.yaml`，文档引用统一链接至 Markdown 台账锚点（`docs/runbooks/source-adaptation-matrix.md#dd-xxx`）。

## 3. 来源适配准入与回归测试红线（`sources/dd-xxx.yaml`）
- **配置契约与台账双向对齐（[`tests/test_storage_and_config.py`](tests/test_storage_and_config.py)）**：所有入库的 `sources/dd-xxx.yaml` 首行必须标注 `# 来源台账：docs/runbooks/source-adaptation-matrix.md#dd-xxx`，通过公网模式（`WLH_ALLOW_LOOPBACK=0`）Schema 校验，且其 `id`、`adapter`、`metadata.document_type` 必须与 [`docs/runbooks/source-adaptation-matrix.csv`](docs/runbooks/source-adaptation-matrix.csv) 严格一致；台账中标记为 `[x] 已实网跑通` 的来源必须存在对应的 `sources/dd-xxx.yaml`，且 `CSV -> MD` 台账保持同步。
- **生产配置本地样本黑盒回放（[`tests/test_source_adapters.py`](tests/test_source_adapters.py)）**：每当新增或修改 `[x] 已实网跑通` 的 `sources/dd-xxx.yaml` 时，必须在 [`tests/test_source_adapters.py`](tests/test_source_adapters.py) 的 `TestAdaptedSourceConfigReplayAndZeroTargetGuard` 中同步登记该 `dd-xxx` 并提供最小真实样本响应，直接加载生产 `sources/dd-xxx.yaml` 原有抽取规则（仅将入口与域名作用域重写至本地测试服务，严禁篡改 `adapter`、`adapter_options`、`documents` 规则或 `metadata`），完整跑通 `collect -> canonical 投影字段断言 -> 断网 reparse（external_requests = 0）` 闭环。
- **零目标发现（`zero_targets_discovered`）防呆红线**：`collect` 与 `reparse` 模式在请求正常完成但发现 `0` 个文档目标时一律判定为失败（`zero_targets_discovered`）；严禁将未发现有效文档目标的空跑标记为适配完成。

