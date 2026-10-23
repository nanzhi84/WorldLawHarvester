# 源名实对齐、交付数据质量修复与固定入口去时间化

> **Status:** Proposal · [Issue #4](https://github.com/nanzhi84/WorldLawHarvester/issues/4) · Created 2026-10-23
> **Affected Architecture:** [docs/architecture/overview.md](../../architecture/overview.md), [docs/architecture/canonical-legal-model.md](../../architecture/canonical-legal-model.md)

## 1. 背景与目标 (Context & Goals)

[Issue #2](../issue#2/spec.md) 与 [Issue #3](../issue#3/spec.md) 关闭后，2026-10-23 对 `main@27bcc01` 的 39 个 `[x] 已实网跑通` 来源做了复评：今日实网探测全部入口，并抽查交付快照 `edd2fe3c`（381 资源 / 449 行）。复评结论是站点与数据均真实，但仍有以下三类问题：

1. **名实偏离**：
   - [dd-512](../../runbooks/source-adaptation-matrix.md#dd-512) 台账名称为越南国会**法律文书系统**，实际适配的是 `duthaoonline.quochoi.vn`（法律**草案**征求意见门户）；生效法律库 `vietlaw.quochoi.vn/Pages/danh-sach-van-ban.aspx` 实测可达（JS Cookie 挑战 `D1N`，共 314 页列表，全文页为 `vbpq-toan-van.aspx?ItemID=`）。
   - [dd-516](../../runbooks/source-adaptation-matrix.md#dd-516)、[dd-556](../../runbooks/source-adaptation-matrix.md#dd-556)、[dd-572](../../runbooks/source-adaptation-matrix.md#dd-572)、[dd-517](../../runbooks/source-adaptation-matrix.md#dd-517)、[dd-570](../../runbooks/source-adaptation-matrix.md#dd-570)、[dd-562](../../runbooks/source-adaptation-matrix.md#dd-562)、[dd-563](../../runbooks/source-adaptation-matrix.md#dd-563)、[dd-565](../../runbooks/source-adaptation-matrix.md#dd-565) 存在替代源、范围收窄或名称超出实际覆盖的情况，台账备注没有如实说明。
   - [dd-509](../../runbooks/source-adaptation-matrix.md#dd-509) 台账登记为“TCP 超时受阻”，今日实测 `law.moj.gov.tw` 返回 HTTP 200。
2. **交付数据质量缺陷**：

   | 来源 | 缺陷 |
   | --- | --- |
   | [dd-455](../../runbooks/source-adaptation-matrix.md#dd-455) | 10/10 行标题均为“国家新闻出版署” |
   | [dd-512](../../runbooks/source-adaptation-matrix.md#dd-512) | 19/29 行标题为 `Tải file đính kèm` |
   | [dd-549](../../runbooks/source-adaptation-matrix.md#dd-549) | 采集到的基本是 `Contents` 目录导航页 |
   | [dd-040](../../runbooks/source-adaptation-matrix.md#dd-040) | 只有期刊目录页，正文中位数约 368 字符 |
   | [dd-492](../../runbooks/source-adaptation-matrix.md#dd-492) | 1 份扫描 PDF 只解析出数字签名块（163 字符），未触发 OCR，也未被质量门禁拦截 |
   | [dd-501](../../runbooks/source-adaptation-matrix.md#dd-501) | 文号字段混入标题文本 |

3. **固定入口**：
   - [dd-501](../../runbooks/source-adaptation-matrix.md#dd-501) 固定在 `issue_13046`；
   - [dd-559](../../runbooks/source-adaptation-matrix.md#dd-559) 固定在 `2024-01-01/title-1`；
   - [dd-560](../../runbooks/source-adaptation-matrix.md#dd-560) 固定 `updated_since=2026-10-01`；
   - [dd-565](../../runbooks/source-adaptation-matrix.md#dd-565) 固定 `2026` 年；
   - [dd-562](../../runbooks/source-adaptation-matrix.md#dd-562) 固定 term `25`；
   - [dd-520](../../runbooks/source-adaptation-matrix.md#dd-520) 固定法人税基本通达第 1 章。

   这些入口能通过反“单篇捷径”门禁，但无法持续跟踪新内容。

**目标**：分 4 个批次修复上述问题。不新增 Adapter 种类，不写站点硬编码分支；做不到的如实登记。

## 2. 方案设计 (Design & Contracts)

### 2.1 约束（沿用 Issue #2 §2.2 三级准入）
- 优先只改 YAML 配置；其次做跨站点通用的最小增强；遇到私有协议或风控一律如实熔断，不写面条代码。
- 新增来源 ID 从 `dd-575` 起递增，同步 `CSV -> MD` 台账，并补 `TestAdaptedSourceConfigReplayAndZeroTargetGuard` 本地回放。
- 回放夹具只改入口与域名，不改生产抽取规则（见 [PROJECTS.md](../../../PROJECTS.md) 第 3 节）。

### 2.2 批次划分

| 批次 | 范围 | 关键交付 |
| --- | --- | --- |
| Batch 1 名实对齐 | dd-512 改为 `vietlaw.quochoi.vn` 生效法律库；草案门户 `duthaoonline.quochoi.vn` 新登记为 `dd-575`；复测 dd-509；dd-516/556/572/517/570/562/563/565 台账备注如实标注 | `sources/dd-512.yaml`、`sources/dd-575.yaml`、台账、回放测试 |
| Batch 2 数据质量 | dd-455 标题；dd-512/dd-575 附件标题回退到父文档标题；dd-501 文号；dd-549 改抓叶子正文页；dd-040 改抓公报文章正文；dd-492 签名块专用文本层的扫描判定（通用增强，放在 `src/wlh/extraction/`） | YAML + 通用提取增强 + 回放断言 |
| Batch 3 去时间化 | dd-501 / dd-559 / dd-560 / dd-565 / dd-562 / dd-520 入口改为“索引 → 最新”或相对时间；不可行的保留并在台账说明 | YAML（必要时做通用相对日期占位能力） |
| Batch 4 收尾 | 全量 40+ 源实网 `collect -> 离线 reparse`、快照、API 核验；更新 Issue #1/#2 spec Status 与 CHANGELOG | 验收工件 `artifacts/issue4/` |

## 3. 真实执行与验收标准 (Verification)

每个批次都须满足：
1. `uv run pytest`（测试库 `wlh_test`，独立 S3 桶）、`uv run ruff check src tests`、`uv run pyright`、`harness doctor` 全部通过。
2. 涉及的来源在独立实网库 `wlh_issue4_verify` 中跑通 `collect -> 离线 reparse`：
   - `status == complete`；
   - `reparse external_requests == 0`；
   - 集合型来源 `resources >= 10`。
   - 收据落在 `artifacts/issue4/batch<N>/`。
3. 规划者使用独立质量审计脚本复核，标准如下：
   - 修复源标题不重复、不是垃圾标题；
   - 正文非空白字符不少于 400，或有合理说明；
   - 没有“仅签名块”正文；
   - 详情页 host 与台账一致。

### 实施 Checklist

#### Batch 1 名实对齐
- [ ] `dd-512` 切换至 `vietlaw.quochoi.vn` 生效法律库，实网 `collect -> reparse` 跑通，落库 `sources/dd-512.yaml`。
- [ ] `dd-575`（越南国会法律草案征求意见门户 `duthaoonline.quochoi.vn`）登记台账并落库 `sources/dd-575.yaml`，实网跑通。
- [ ] `dd-509` 实网复测：跑通则适配落库，否则更新受阻证据。
- [ ] dd-516/556/572/517/570/562/563/565 台账备注如实标注替代源或范围收窄，`CSV -> MD` 同步。
- [ ] 回放测试补齐 dd-512 / dd-575（及 dd-509，如跑通）。

#### Batch 2 数据质量
- [ ] `dd-455` 每行标题为具体公示标题，不再全部为发布机构名。
- [ ] `dd-512` / `dd-575` 附件行标题不再是 `Tải file đính kèm`。
- [ ] `dd-501` 文号字段只含文号。
- [ ] `dd-549` 采集叶子手册正文页，不再是 `Contents` 目录页。
- [ ] `dd-040` 采集公报文章正文，不再只有期刊目录。
- [ ] 签名块专用文本层的 PDF 判定为疑似扫描并走 OCR，或明确报错；`dd-492` 不再产出仅签名块正文。

#### Batch 3 去时间化
- [ ] `dd-501` 从公报总目录发现最新一期。
- [ ] `dd-559` 不再固定 `2024-01-01` 与仅 Title 1。
- [ ] `dd-560` 不再固定 `updated_since` 绝对日期。
- [ ] `dd-565` / `dd-562` / `dd-520` 去除固定年份、term 或章节，或在台账如实说明保留原因。

#### Batch 4 收尾
- [ ] 全量已跑通来源实网 `collect -> 离线 reparse`、快照发布与 `GET /results?projection=canonical` 核验。
- [ ] Issue #1/#2 spec Status 更新为 Implemented，CHANGELOG 补记 Issue #2~#4。
