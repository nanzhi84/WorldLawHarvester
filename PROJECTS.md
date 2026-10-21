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

