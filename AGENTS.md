# Agent 协作与仓库规范 (Universal Harness)

未经授权，严禁修改本文件及各目录下的规范说明。

## 1. 核心工作流与验收纪律
- **静态与契约检查**：任何代码或文档变更、移动后，必须优先通过类型检查、Linter 以及结构、文本与死链巡检（运行 `harness doctor`；可通过 `harness rule --all` 查阅规则细则，确需破例时在文档中使用 `<!-- harness-ignore CODE: 原因 -->` 标注豁免）。
- **真实执行验收（重运行、轻测试代码）**：完成实质业务改动后，严禁编写依赖 Mock 或拼凑覆盖率的无效单元测试。主 Agent 必须利用 `work/` 目录构造真实或最小可复现输入（可通过 `harness run <task> -- <cmd>` 自动隔离运行），直接执行 CLI、接口或端到端流程，通过核对 `artifacts/` 中的实际输出产物、日志或状态回执来完成自验。
- **高价值黑盒测试**：仅在核心数据流、复杂解析器或极易回归的边界条件下，才在 `tests/` 中补充测试。测试必须聚焦于真实输入与输出的黑盒契约，禁止针对内部实现细节编写脆弱断言。

## 2. 根目录工作区约定
- `README.md`：项目门面介绍（居中 Logo 与简介、`Core ideas`、`Get started`、`Learn more`）。
- `PROJECTS.md`：当前项目的特异业务规则、技术栈约束与领域红线（自由编写）。
- `tests/`：仅存放核心链路的高价值黑盒与集成测试。
- `work/` & `artifacts/`：日常开发与端到端执行验收产生的临时运行数据和构建产物（已忽略不入库，随时可清理）。
- `experiments/<name>/`：独立的技术选型与原型探索沙盒，内部环境与配置自包含，严禁为日常功能改动新建实验。
- `CHANGELOG.md`：集中记录版本发布、能力演进与破坏性变更。

## 3. 渐进式上下文路由（按需读取）
请根据当前任务性质，主动读取以下规范，未涉及的领域无需加载：
- **开始任何业务代码编写、修改数据流或外部集成前**：必须先读取根目录的 [PROJECTS.md](PROJECTS.md) 与 [docs/architecture/](docs/architecture)，严格遵守当前项目的领域红线与架构现状。
- **读写、移动或归档任何文档前**：必须先读取 [docs/README.md](docs/README.md)，严格遵守 `architecture`、`spec`（`issue#<N>/`）、`adr`、`runbooks`、`reference` 的生命周期与归档纪律（可使用 `harness spec new`、`harness adr new`、`harness adr supersede` 辅助流转）。
