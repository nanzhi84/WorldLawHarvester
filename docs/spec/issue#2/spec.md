# Wave 2 多法域高优来源规模化适配计划（40 源四批次矩阵 · 越南全量 P0 优先）

> **Status:** Proposal · [Issue #2](https://github.com/nanzhi84/WorldLawHarvester/issues/2) · Created 2026-10-22
> **Affected Architecture:** [docs/architecture/overview.md](../../architecture/overview.md), [docs/architecture/canonical-legal-model.md](../../architecture/canonical-legal-model.md)

## 1. 背景与目标 (Context & Goals)

### 1.1 现状基线
在 [Issue #1 首批架构与分阶段实施计划](../issue#1/spec.md) 及后续架构收敛中，`WorldLawHarvester` (`wlh`) 已完成：
- **统一适配器底座（8 个适配器/桥接器）**：原生适配器（`web`、`federal_register`、`structured_xml`）与五大开源桥接器（`worldwidelaw_bridge`、`juriscraper_bridge`、`oldp_bridge`、`opencaselaw_bridge`、`openstates_bridge`），其中原越南专属适配器已收敛合并至通用 [`src/wlh/adapters/web.py`](../../../src/wlh/adapters/web.py) 与 [`src/wlh/adapters/bridges/worldwidelaw_bridge.py`](../../../src/wlh/adapters/bridges/worldwidelaw_bridge.py)。
- **三道防退化与防呆门禁**（见 [PROJECTS.md](../../../PROJECTS.md) 第 3 节）：
  1. `collect` / `reparse` 零目标发现防呆门禁（`zero_targets_discovered` $\rightarrow$ `status = "failed"`、`enumeration_incomplete`）；
  2. 全量 `sources/dd-xxx.yaml` 公网 Schema 校验与 [多法域资源适配与推进台账](../../runbooks/source-adaptation-matrix.md) 双向对齐（[`tests/test_storage_and_config.py`](../../../tests/test_storage_and_config.py)）；
  3. 生产配置本地真实样本黑盒回放（[`tests/test_source_adapters.py`](../../../tests/test_source_adapters.py) 的 `TestAdaptedSourceConfigReplayAndZeroTargetGuard`）。
- **台账覆盖现状**：[多法域资源适配与推进台账（CSV）](../../runbooks/source-adaptation-matrix.csv) 共登记 574 个来源，当前已实网跑通 8 个来源（[dd-017](../../runbooks/source-adaptation-matrix.md#dd-017)、[dd-492](../../runbooks/source-adaptation-matrix.md#dd-492)、[dd-493](../../runbooks/source-adaptation-matrix.md#dd-493)、[dd-499](../../runbooks/source-adaptation-matrix.md#dd-499)、[dd-551](../../runbooks/source-adaptation-matrix.md#dd-551)、[dd-556](../../runbooks/source-adaptation-matrix.md#dd-556)、[dd-560](../../runbooks/source-adaptation-matrix.md#dd-560)、[dd-561](../../runbooks/source-adaptation-matrix.md#dd-561)），实测受阻 1 个（[dd-494](../../runbooks/source-adaptation-matrix.md#dd-494)）。

### 1.2 Wave 2 核心目标（40 个候选源，越南法域全量 P0 打头阵）
按照内部交付死线（DDL）优先级要求，**越南法域全部 12 个来源统一提级为 `P0`**；同时考虑到跨国政务与司法站点在实网采集中存在地域 IP 封锁、Cloudflare/WAF 动态挑战或强制验证码等客观验收损耗，本轮计划将候选池扩大至 **40 个高优来源（分为 Batch A / B / C / D 四个批次）**：
1. **Batch A（10 个源 · 第一优先级 P0）：越南法域全量未跑通源 100% 入列攻坚**：将除已跑通的 `dd-492`、`dd-493` 外的其余 **10 个越南法域来源**（`dd-494`～`dd-498`、`dd-512`～`dd-516`，涵盖越南国会、政府政策专栏、最高人民法院判例/判决库、财政部/税务总局、VBPL、LuatVietnam、Thư Viện Pháp Luật）全部纳入第一批次，充分发挥已打磨成熟的越语 Office/TCVN3 恢复与越语 OCR 门禁能力。
2. **Batch B（9 个源）：官方结构化立法/判例 XML 与开放数据批次（`structured_xml` 生产破零）**：覆盖英国、德国、日本、美国、加拿大、新西兰及非洲联盟的官方立法/判例 XML 与开放 API。
3. **Batch C（9 个源）：中国大陆中央政务、司法与部委公开栏目批次**：覆盖国务院公报、国家规章库、最高检指导性案例/公报、外汇局行政处罚、财政部地方债、国家统计局行业分类标准及电影局/版署审批公示，补齐 `政策文件`、`通知公告`、`行业标准` 实网样本。
4. **Batch D（12 个源）：港澳台、亚太及欧美澳加最高法院与税务公报批次**：覆盖香港司法机构 LRS、香港税务局 DIPN、澳门印务局、台湾全国法规资料库、日本国税厅 NTA、新加坡税务局 IRAS、英国 HMRC、德国联邦财政部、法国 BOFiP 税务库、美国最高法院/IRS IRB 与加拿大最高法院 SCC，补齐跨法域 `税务资料` 与英美法系最高法院判例样本。
5. **诚实分流与三道回归红线锁死**：实网跑通的源标记为 `[x] 已实网跑通` 并入库 `sources/dd-xxx.yaml` + 补齐 `TestAdaptedSourceConfigReplayAndZeroTargetGuard` 本地样本回放；实网遭遇地域封锁/WAF/强验证码阻塞的源诚实标记为 `[ ] 实测受阻` 并记录精确错误码与原因。

---

## 2. 方案设计 (Design & Contracts)

### 2.1 Wave 2 候选来源四批次矩阵（共 40 个候选源）

#### Batch A：越南法域全量 P0 攻坚批次（10 个源 · 覆盖越南全部剩余来源）

| 序号 | 台账编号 | 优先级 | 法域 / 国家 | 来源名称与官方入口 | 资料类型 | 适配器 | 适配方案与验证重点 |
| :---: | :--- | :---: | :--- | :--- | :--- | :--- | :--- |
| 1 | **[dd-512](../../runbooks/source-adaptation-matrix.md#dd-512)** | P0 | 1.4 越南 | **越南国会法律文书系统 (`Văn bản quy phạm pháp luật Quốc hội`)**<br>`https://quochoi.vn/tintuc/Pages/van-ban-phap-luat.aspx` | 法律法规 | `web` | 越南国会 SharePoint/ASP.NET 公开法律文书列表、详情元数据与 PDF/DOC 附件提取 |
| 2 | **[dd-513](../../runbooks/source-adaptation-matrix.md#dd-513)** | P0 | 1.4 越南 | **越南政府门户政策与法规建设专栏 (`Xây dựng chính sách, pháp luật`)**<br>`https://xaydungchinhsach.chinhphu.vn/` | 法律法规 | `web` | 越南政府法规政策专栏公开列表、详情正文与原件附件采集 |
| 3 | **[dd-514](../../runbooks/source-adaptation-matrix.md#dd-514)** | P0 | 1.4 越南 | **越南最高人民法院判例门户 (`Án lệ TANDTC`)**<br>`https://anle.toaan.gov.vn/` | 案例 | `web` | 越南最高法院公开指导判例（Án lệ）列表发现、详情与 PDF/DOC 附件提取 |
| 4 | **[dd-516](../../runbooks/source-adaptation-matrix.md#dd-516)** | P0 | 1.4 越南 | **越南财政部 / 税务总局法规与公文库 (`Bộ Tài chính / Tổng cục Thuế`)**<br>`https://gdt.gov.vn/` | 税务资料 | `web` | 越南税务总局/财政部公开税务法规与公文（Công văn）列表、详情及附件采集 |
| 5 | **[dd-495](../../runbooks/source-adaptation-matrix.md#dd-495)** | P0 | 1.4 越南 | **LuatVietnam 法规库**<br>`https://luatvietnam.vn/` | 法律法规 | `web` | 越南主流法规门户最新公开文书列表与详情元数据/正文受控采集 |
| 6 | **[dd-496](../../runbooks/source-adaptation-matrix.md#dd-496)** | P0 | 1.4 越南 | **LuatVietnam 判决库**<br>`https://luatvietnam.vn/ban-an/` | 案例 | `web` | 越南公开裁判文书（Bản án）列表发现与详情提取 |
| 7 | **[dd-497](../../runbooks/source-adaptation-matrix.md#dd-497)** | P0 | 1.4 越南 | **Thư Viện Pháp Luật 法规库**<br>`https://thuvienphapluat.vn/page/tim-van-ban.aspx` | 法律法规 | `web` | 越南大型法规索引库公开法规列表与文号/发布日期/正文受控采集（检验反爬/WAF 边界） |
| 8 | **[dd-498](../../runbooks/source-adaptation-matrix.md#dd-498)** | P0 | 1.4 越南 | **Thư Viện Pháp Luật 判决库**<br>`https://thuvienphapluat.vn/banan/` | 案例 | `web` | 越南公开判决索引列表与详情正文采集（检验反爬/WAF 边界） |
| 9 | **[dd-515](../../runbooks/source-adaptation-matrix.md#dd-515)** | P0 | 1.4 越南 | **越南最高人民法院电子门户公开判决库 (`Công bố bản án, quyết định`)**<br>`https://congbobanan.toaan.gov.vn/` | 案例 | `web` | 尝试公开免验证码列表/详情入口采集，若遇强制图形验证码拦截则精确取证受阻边界 |
| 10 | **[dd-494](../../runbooks/source-adaptation-matrix.md#dd-494)** | P0 | 1.4 越南 | **越南国家法律数据库 (`VBPL - vbpl.vn`)**<br>`https://vbpl.vn/` | 法律法规 | `web` | 复测 HTTP/HTTPS 镜像与子级部委/地方入口连接可达性，若仍为源站重置连接则更新复测证据 |

#### Batch B：官方结构化立法/判例 XML 与开放数据批次（9 个源）

| 序号 | 台账编号 | 优先级 | 法域 / 国家 | 来源名称与官方入口 | 资料类型 | 适配器 | 适配方案与验证重点 |
| :---: | :--- | :---: | :--- | :--- | :--- | :--- | :--- |
| 11 | **[dd-547](../../runbooks/source-adaptation-matrix.md#dd-547)** | P0 | 2.2 英国 | **英国国家立法库 (`legislation.gov.uk`)**<br>`https://www.legislation.gov.uk/` | 法律法规 | `structured_xml` | 通过公开 `/data.xml` / `.akn` 端点获取 Akoma Ntoso / Legislation XML，提取法条正文、文号与颁布日期 |
| 12 | **[dd-548](../../runbooks/source-adaptation-matrix.md#dd-548)** | P0 | 2.2 英国 | **英国国家档案馆判例库 (`Find Case Law`)**<br>`https://caselaw.nationalarchives.gov.uk/` | 案例 | `structured_xml` | 通过 Atom Feed / 公开判例 `.xml` 获取 Akoma Ntoso 3.0 `<judgment>`，提取中性引证号、法院与判决全文 |
| 13 | **[dd-550](../../runbooks/source-adaptation-matrix.md#dd-550)** | P0 | 2.3 德国 | **德国联邦司法部法律库 (`Gesetze im Internet`)**<br>`https://www.gesetze-im-internet.de/` | 法律法规 | `structured_xml` | 支持内存安全读取 `.zip` 内 `BJNR*.xml`（`<dokumente><norm>` 结构），提取法典简称（`jurabk`）、标题与法条正文并保留 `.zip` 原件 |
| 14 | **[dd-517](../../runbooks/source-adaptation-matrix.md#dd-517)** | P0 | 1.5 日本 | **e-Gov 法令检索 / 法令 API v2**<br>`https://laws.e-gov.go.jp/` | 法律法规 | `structured_xml` | 对接公开免 Key 法令 XML 接口，验证 `<Law>` / `<LawNum>` / `<LawTitle>` / `<MainProvision>` 日文法令解析 |
| 15 | **[dd-558](../../runbooks/source-adaptation-matrix.md#dd-558)** | P0 | 3.1 美国 | **美国众议院《美国法典》官方门户 (`uscode.house.gov`)**<br>`https://uscode.house.gov/download/download.shtml` | 法律法规 | `structured_xml` | 解析官方 USLM 1.0 XML（或 ZIP 封装 XML），提取 Title/Section 结构化正文与引证号 |
| 16 | **[dd-559](../../runbooks/source-adaptation-matrix.md#dd-559)** | P0 | 3.1 美国 | **电子联邦法规汇编 (`eCFR API - ecfr.gov`)**<br>`https://www.ecfr.gov/developers/documentation/api/v1` | 法律法规 | `structured_xml` | 对接公开 `/api/versioner/v1/full/...` XML 端点，提取 CFR Title/Part 法规正文与生效日期 |
| 17 | **[dd-564](../../runbooks/source-adaptation-matrix.md#dd-564)** | P0 | 3.2 加拿大 | **加拿大司法部联邦法规库 (`Justice Laws Website`)**<br>`https://laws-lois.justice.gc.ca/` | 法律法规 | `structured_xml` | 对接公开 `/eng/XML/...` 官方立法 XML，提取加拿大联邦法案/规章标题、章号与条文正文 |
| 18 | **[dd-570](../../runbooks/source-adaptation-matrix.md#dd-570)** | P0 | 4.2 新西兰 | **新西兰立法门户 (`New Zealand Legislation - PCO`)**<br>`https://www.legislation.govt.nz/` | 法律法规 | `structured_xml` | 对接公开 `/subscribe/act/...` 官方 XML 索引与法案 XML，验证大洋洲立法结构化提取 |
| 19 | **[dd-572](../../runbooks/source-adaptation-matrix.md#dd-572)** | P1 | 5.1 非洲联盟 | **Laws.Africa Commons & 非洲 LII 联盟**<br>`https://commons.laws.africa/` | 法律法规 | `structured_xml` | 验证跨法域标准 Akoma Ntoso 3.0 立法 XML 采集与 16 列标准映射 |

#### Batch C：中国大陆中央立法、司法、部委与监管公开栏目批次（9 个源）

| 序号 | 台账编号 | 优先级 | 法域 / 国家 | 来源名称与官方入口 | 资料类型 | 适配器 | 适配方案与验证重点 |
| :---: | :--- | :---: | :--- | :--- | :--- | :--- | :--- |
| 20 | **[dd-501](../../runbooks/source-adaptation-matrix.md#dd-501)** | P0 | 1.1 中国大陆 | **中华人民共和国国务院公报**<br>`https://www.gov.cn/gongbao/` | 政策文件 | `web` | 静态 HTML 列表与公报正文页，提取发文字号、发文机关、成文/发布日期与正文 |
| 21 | **[dd-502](../../runbooks/source-adaptation-matrix.md#dd-502)** | P1 | 1.1 中国大陆 | **国家规章库**<br>`https://www.gov.cn/zhengce/xxgk/gjgzk/index.htm` | 法律法规 | `web` | 国务院部门规章与地方政府规章公开详情页/PDF 附件有界采集 |
| 22 | **[dd-041](../../runbooks/source-adaptation-matrix.md#dd-041)** | P0 | 1.1 中国大陆 | **最高人民检察院指导性案例**<br>`https://www.spp.gov.cn/spp/jczdal/index.shtml` | 案例 | `web` | 静态 HTML 列表 + 详情正文页，XPath + 正则提取“检例第X号”及发布日期 |
| 23 | **[dd-040](../../runbooks/source-adaptation-matrix.md#dd-040)** | P1 | 1.1 中国大陆 | **最高人民检察院公报**<br>`https://www.spp.gov.cn/spp/gjgb/index.shtml` | 政策文件 | `web` | 与 `dd-041` 同站同构，验证同站点多栏目 YAML 规则复用 |
| 24 | **[dd-102](../../runbooks/source-adaptation-matrix.md#dd-102)** | P1 | 1.1 中国大陆 | **国家外汇管理局（行政处罚与监管信息公开）**<br>`https://www.safe.gov.cn/beijing/xzcfxxgs/index.html` | 通知公告 | `web` | 静态 HTML 列表 + 详情页，补齐 `通知公告` 资料类型实网样本 |
| 25 | **[dd-183](../../runbooks/source-adaptation-matrix.md#dd-183)** | P1 | 1.1 中国大陆 | **财政部预算司地方债管理专题栏目**<br>`http://yss.mof.gov.cn/zhuantilanmu/dfzgl/` | 政策文件 | `web` | 财政部专题列表 + 政策正文/附件采集，验证财税/地方债政策文档提取 |
| 26 | **[dd-375](../../runbooks/source-adaptation-matrix.md#dd-375)** | P1 | 1.1 中国大陆 | **国家统计局国民经济行业分类统计标准**<br>`https://www.stats.gov.cn/xxgk/tjbz/gjtjbz/201905/t20190521_1758938.html` | 行业标准 | `web` | 国家统计标准公开详情与表格/附件提取，补齐 `行业标准` 资料类型实网样本 |
| 27 | **[dd-448](../../runbooks/source-adaptation-matrix.md#dd-448)** | P2 | 1.1 中国大陆 | **国家电影局电影备案立项公示**<br>`https://www.chinafilm.gov.cn/xxgk/gsxx/dybalx/` | 通知公告 | `web` | 行业监管备案公示列表 + 详情正文/表格采集 |
| 28 | **[dd-455](../../runbooks/source-adaptation-matrix.md#dd-455)** | P2 | 1.1 中国大陆 | **国家新闻出版署游戏审批结果公示**<br>`https://www.nppa.gov.cn/bsfw/jggs/yxspjg/` | 通知公告 | `web` | 审批结果公示列表 + 含大型 HTML 表格的详情页 Markdown 转换验证 |

#### Batch D：港澳台、亚太及欧美澳加最高法院与税务公报批次（12 个源）

| 序号 | 台账编号 | 优先级 | 法域 / 国家 | 来源名称与官方入口 | 资料类型 | 适配器 | 适配方案与验证重点 |
| :---: | :--- | :---: | :--- | :--- | :--- | :--- | :--- |
| 29 | **[dd-506](../../runbooks/source-adaptation-matrix.md#dd-506)** | P0 | 1.2 中国香港 | **香港司法机构法律参考资料系统 (`LRS`)**<br>`https://legalref.judiciary.hk/` | 案例 | `web` / `worldwidelaw_bridge` | 香港法院判案书公开列表与详情/Word/PDF 提取 |
| 30 | **[dd-507](../../runbooks/source-adaptation-matrix.md#dd-507)** | P1 | 1.2 中国香港 | **香港税务局 (`IRD`) 释义及执行指引 (`DIPN`)**<br>`https://www.ird.gov.hk/` | 税务资料 | `web` | 公开税务执行指引列表与 PDF 原件采集，补齐 `税务资料` 实网样本 |
| 31 | **[dd-508](../../runbooks/source-adaptation-matrix.md#dd-508)** | P1 | 1.2 中国澳门 | **澳门印务局 (`Imprensa Oficial`) 公报与法例**<br>`https://www.io.gov.mo/` | 法律法规 | `web` / `worldwidelaw_bridge` | 澳门特区公报及法例中葡双语公开页面与 PDF 原件采集 |
| 32 | **[dd-509](../../runbooks/source-adaptation-matrix.md#dd-509)** | P0 | 1.3 中国台湾 | **全国法规资料库 (`LawBank Taiwan`)**<br>`https://law.moj.gov.tw/ENG/index.aspx` | 法律法规 | `web` / `structured_xml` | 台湾最新法规/英译法规公开列表与条文详情提取 |
| 33 | **[dd-520](../../runbooks/source-adaptation-matrix.md#dd-520)** | P1 | 1.5 日本 | **日本国税厅法令解释通达 (`NTA`)**<br>`https://www.nta.go.jp/law/index.htm` | 税务资料 | `web` | 日本国税厅公开基本通达/文书回答事例 HTML/PDF 采集 |
| 34 | **[dd-527](../../runbooks/source-adaptation-matrix.md#dd-527)** | P1 | 1.7 新加坡 | **新加坡国内税务局 (`IRAS`) e-Tax Guides**<br>`https://www.iras.gov.sg/` | 税务资料 | `web` | 新加坡税务指南公开页面与 PDF 原件采集 |
| 35 | **[dd-549](../../runbooks/source-adaptation-matrix.md#dd-549)** | P1 | 2.2 英国 | **英国税务海关总署内部手册 (`HMRC Internal Manuals`)**<br>`https://www.gov.uk/government/collections/hmrc-manuals` | 税务资料 | `web` | GOV.UK 公开税务手册列表与章节详情 HTML 提取 |
| 36 | **[dd-552](../../runbooks/source-adaptation-matrix.md#dd-552)** | P1 | 2.3 德国 | **德国联邦财政部 (`BMF-Schreiben`) 与联邦财税法院**<br>`https://www.bundesfinanzministerium.de/` | 税务资料 | `web` | 德国财政部公开税务通函（BMF-Schreiben）列表与 PDF 原件采集 |
| 37 | **[dd-555](../../runbooks/source-adaptation-matrix.md#dd-555)** | P0 | 2.4 法国 | **法国公共财政官方公报税务库 (`BOFiP-Impôts`)**<br>`https://bofip.impots.gouv.fr/` | 税务资料 | `web` / `structured_xml` | 法国官方税务公报公开文档/列表采集 |
| 38 | **[dd-562](../../runbooks/source-adaptation-matrix.md#dd-562)** | P1 | 3.1 美国 | **美国联邦最高法院官网 (`supremecourt.gov`)**<br>`https://www.supremecourt.gov/opinions/opinions.aspx` | 案例 | `web` | 最高法院 Slip Opinions 公开列表与判决书 PDF 原件采集 |
| 39 | **[dd-563](../../runbooks/source-adaptation-matrix.md#dd-563)** | P0 | 3.1 美国 | **美国国税局 (`IRS Internal Revenue Bulletins`)**<br>`https://www.irs.gov/irb` | 税务资料 | `web` | IRS 官方每周税务公报（IRB）HTML/PDF 采集 |
| 40 | **[dd-565](../../runbooks/source-adaptation-matrix.md#dd-565)** | P1 | 3.2 加拿大 | **加拿大最高法院 (`SCC / Lexum`)**<br>`https://decisions.scc-csc.ca/` | 案例 | `web` | 加拿大最高法院公开裁判列表与详情/PDF 采集 |

---

### 2.2 三级适配准入与诚实熔断机制（严禁新增 Adapter 或硬编码路由）

在实网推进 40 个来源时，严格坚守 **“最小化通用增强、零面条代码、零硬编码域名分支”** 纪律，按以下三级准则决策：

1. **第一级：零代码纯 YAML 配置（优先）**
   - 凡是标准 HTML 列表+详情+附件、ASP.NET `__doPostBack` 分页、或现有 `structured_xml` / Bridge 已支持的格式，仅编写 `sources/dd-xxx.yaml`（配合 `fetch.mode: http` 或 `rendered_dom`）。
2. **第二级：最小化跨站点通用能力增强（严禁新增 Adapter 种类）**
   - 仅当某项增强具备**跨多个法域/站点的通用性**且不引入任何站点特异硬编码时，才允许合入既有适配器：
     - **[`src/wlh/adapters/structured_xml.py`](../../../src/wlh/adapters/structured_xml.py)**：支持内存安全 `.zip` 内 XML 解包（复用于德国 `dd-550`、美国 `dd-558`）、HTML/Atom/XML 索引页发现子级 `.xml`/`.akn` 详情链接（配合 `documents.detail_url_patterns` 复用于英国 `dd-547`/`dd-548`、新西兰 `dd-570`），以及多国标准立法/判例 XML 根节点（`<norm>`、`<Statute>`、`<DIV1>`–`<DIV8>`、`<judgment>`）映射；
     - **[`src/wlh/adapters/web.py`](../../../src/wlh/adapters/web.py)**：在 `parse_date()` 中扩展多语种常见日期格式（如中文 `%Y年%m月%d日`、英文 `%B %d, %Y` / `%d %B %Y` / `%Y/%m/%d`）的自动归一化。
3. **第三级：特异协议或外部风控一律诚实熔断（`[ ] 实测受阻`，绝不写面条代码）**
   - 若实测发现某来源存在以下任一情形，**一律视为验收不通过**，严禁为了强行跑通而编写单站点面条代码、硬编码域名路由或新增专属 Adapter：
     - **协议特异无法通用化**：站点依赖私有加密参数、非标多步异构接口拼接或必须硬编码单站点 DOM/JSON 分支才能提取；
     - **外部风控与连接阻断**：源站 TCP 重置（如 `dd-494`）、Cloudflare Turnstile / WAF 强交互拦截、强制图形验证码（如 `dd-515` 可能遇到的情形）或强制登录授权墙。
   - 触发熔断的来源在 Checklist 中诚实保持 `[ ]` 未勾选，在 [`docs/runbooks/source-adaptation-matrix.csv`](../../runbooks/source-adaptation-matrix.csv) 中登记为 `[ ] 实测受阻` 并记录精确错误码与受阻原因。

---

## 3. 分阶段实施 Checklist 与真实执行验收标准 (Verification)

### Phase 1（Batch A · P0 第一优先）：越南法域全量未跑通源攻坚批次（10 个源）
- [x] `dd-512`（越南国会法律文书系统 `quochoi.vn`）实网 `collect -> reparse` 验证与 `sources/dd-512.yaml` 落库。
- [x] `dd-513`（越南政府门户政策与法规建设专栏 `xaydungchinhsach.chinhphu.vn`）实网 `collect -> reparse` 验证与 `sources/dd-513.yaml` 落库。
- [x] `dd-514`（越南最高人民法院判例门户 `anle.toaan.gov.vn`）实网 `collect -> reparse` 验证与 `sources/dd-514.yaml` 落库。
- [x] `dd-516`（越南财政部 / 税务总局法规与公文库 `gdt.gov.vn`）实网 `collect -> reparse` 验证与 `sources/dd-516.yaml` 落库。
- [x] `dd-495`（LuatVietnam 法规库 `luatvietnam.vn`）实网 `collect -> reparse` 验证与 `sources/dd-495.yaml` 落库。
- [x] `dd-496`（LuatVietnam 判决库 `luatvietnam.vn/ban-an/`）实网 `collect -> reparse` 验证与 `sources/dd-496.yaml` 落库。
- [ ] `dd-497`（Thư Viện Pháp Luật 法规库 `thuvienphapluat.vn`）实网 `collect -> reparse` 验证与 `sources/dd-497.yaml` 落库。
- [ ] `dd-498`（Thư Viện Pháp Luật 判决库 `thuvienphapluat.vn/banan/`）实网 `collect -> reparse` 验证与 `sources/dd-498.yaml` 落库。
- [ ] `dd-515`（越南最高人民法院公开判决库 `congbobanan.toaan.gov.vn`）实网验证与 `sources/dd-515.yaml` 落库（若受阻则如实记录验证码/WAF 边界）。
- [ ] `dd-494`（越南国家法律数据库 `vbpl.vn`）复测连接可达性与备选入口。
- [x] 在 `tests/test_source_adapters.py` 中补齐 Batch A 所有实网跑通源的本地样本黑盒回放，并同步更新 `docs/runbooks/source-adaptation-matrix.csv` 与 `.md`。

### Phase 2（Batch B）：官方结构化立法/判例 XML 批次（9 个源）
- [x] 完成 `src/wlh/adapters/structured_xml.py` 对 `.zip` 法规包、索引链接发现及多国官方立法/判例 XML 方言（UK / DE / JP / US eCFR / CA / NZ / AKN judgment）的通用支持。
- [ ] `dd-547`（英国国家立法库 `legislation.gov.uk`）实网 `collect -> reparse` 验证与 `sources/dd-547.yaml` 落库。
- [x] `dd-548`（英国国家档案馆判例库 `caselaw.nationalarchives.gov.uk`）实网 `collect -> reparse` 验证与 `sources/dd-548.yaml` 落库。
- [x] `dd-550`（德国联邦司法部法律库 `gesetze-im-internet.de`）实网 `collect -> reparse` 验证与 `sources/dd-550.yaml` 落库。
- [x] `dd-517`（日本 e-Gov 法令 API v2 `laws.e-gov.go.jp`）实网 `collect -> reparse` 验证与 `sources/dd-517.yaml` 落库。
- [x] `dd-558`（美国众议院《美国法典》门户 `uscode.house.gov`）实网 `collect -> reparse` 验证与 `sources/dd-558.yaml` 落库。
- [x] `dd-559`（美国电子联邦法规汇编 `ecfr.gov`）实网 `collect -> reparse` 验证与 `sources/dd-559.yaml` 落库。
- [x] `dd-564`（加拿大司法部联邦法规库 `laws-lois.justice.gc.ca`）实网 `collect -> reparse` 验证与 `sources/dd-564.yaml` 落库。
- [x] `dd-570`（新西兰立法门户 `legislation.govt.nz`）实网 `collect -> reparse` 验证与 `sources/dd-570.yaml` 落库。
- [x] `dd-572`（非洲开放法律联盟 `commons.laws.africa`）实网 `collect -> reparse` 验证与 `sources/dd-572.yaml` 落库。
- [x] 在 `tests/test_source_adapters.py` 中补齐 Batch B 所有实网跑通源的本地样本黑盒回放，并同步更新台账。

### Phase 3（Batch C）：中国大陆中央政务、司法与监管公开栏目批次（9 个源）
- [x] 完成 `src/wlh/adapters/web.py` 的 `parse_date()` 中文/英文多格式日期归一化扩展。
- [x] `dd-501`（中华人民共和国国务院公报）实网 `collect -> reparse` 验证与 `sources/dd-501.yaml` 落库。
- [x] `dd-502`（国家规章库）实网 `collect -> reparse` 验证与 `sources/dd-502.yaml` 落库。
- [x] `dd-041`（最高人民检察院指导性案例）实网 `collect -> reparse` 验证与 `sources/dd-041.yaml` 落库。
- [x] `dd-040`（最高人民检察院公报）实网 `collect -> reparse` 验证与 `sources/dd-040.yaml` 落库。
- [x] `dd-102`（国家外汇管理局行政处罚与监管信息公开）实网 `collect -> reparse` 验证与 `sources/dd-102.yaml` 落库。
- [x] `dd-183`（财政部预算司地方债管理专题栏目）实网 `collect -> reparse` 验证与 `sources/dd-183.yaml` 落库。
- [x] `dd-375`（国家统计局国民经济行业分类统计标准）实网 `collect -> reparse` 验证与 `sources/dd-375.yaml` 落库。
- [x] `dd-448`（国家电影局电影备案立项公示）实网 `collect -> reparse` 验证与 `sources/dd-448.yaml` 落库。
- [x] `dd-455`（国家新闻出版署游戏审批结果公示）实网 `collect -> reparse` 验证与 `sources/dd-455.yaml` 落库。
- [x] 在 `tests/test_source_adapters.py` 中补齐 Batch C 所有实网跑通源的本地样本黑盒回放，并同步更新台账。

### Phase 4（Batch D）：港澳台、亚太及欧美澳加最高法院与税务公报批次（12 个源）及全局快照验收
- [x] `dd-506`（香港司法机构法律参考资料系统 LRS）实网 `collect -> reparse` 验证与 `sources/dd-506.yaml` 落库。
- [x] `dd-507`（香港税务局释义及执行指引 DIPN）实网 `collect -> reparse` 验证与 `sources/dd-507.yaml` 落库。
- [ ] `dd-508`（澳门印务局公报与法例）实网 `collect -> reparse` 验证与 `sources/dd-508.yaml` 落库。
- [ ] `dd-509`（中国台湾全国法规资料库）实网 `collect -> reparse` 验证与 `sources/dd-509.yaml` 落库。
- [x] `dd-520`（日本国税厅法令解释通达 NTA）实网 `collect -> reparse` 验证与 `sources/dd-520.yaml` 落库。
- [x] `dd-527`（新加坡国内税务局 IRAS e-Tax Guides）实网 `collect -> reparse` 验证与 `sources/dd-527.yaml` 落库。
- [x] `dd-549`（英国税务海关总署内部手册 HMRC Internal Manuals）实网 `collect -> reparse` 验证与 `sources/dd-549.yaml` 落库。
- [ ] `dd-552`（德国联邦财政部 BMF-Schreiben）实网 `collect -> reparse` 验证与 `sources/dd-552.yaml` 落库。
- [ ] `dd-555`（法国公共财政官方公报税务库 BOFiP-Impôts）实网 `collect -> reparse` 验证与 `sources/dd-555.yaml` 落库。
- [x] `dd-562`（美国联邦最高法院官网 `supremecourt.gov`）实网 `collect -> reparse` 验证与 `sources/dd-562.yaml` 落库。
- [x] `dd-563`（美国国税局 IRS Internal Revenue Bulletins）实网 `collect -> reparse` 验证与 `sources/dd-563.yaml` 落库。
- [x] `dd-565`（加拿大最高法院 SCC）实网 `collect -> reparse` 验证与 `sources/dd-565.yaml` 落库。
- [x] 在 `tests/test_source_adapters.py` 中补齐 Batch D 所有实网跑通源的本地样本黑盒回放，并同步更新台账。
- [x] 执行全局 `wlh snapshot create`、`wlh snapshot publish` 与 `GET /results?projection=canonical` 端到端核验，通过 `uv run pytest`、`uv run ruff check src tests` 与 `harness doctor`。

### 验收与损耗诚实登记纪律
1. **实网隔离运行**：每个来源均通过 `harness run <source-id>-live -- ...` 执行有界实网 `wlh collect` 与断网 `wlh reparse`，核对 `artifacts/` 下 `receipt.json` 中 `status == "complete"`、`resources >= 1` 且 `reparse` 的 `external_requests == 0`。
2. **诚实记录实网损耗（`[ ] 实测受阻`）**：40 个候选源在实网验收中若遇到目标站点地域 IP 封锁、强制动态 WAF/Cloudflare Turnstile 挑战、登录/授权拦截或 TLS 握手阻断，严禁伪造通过；必须在 Checklist 中诚实保持 `[ ]` 未勾选，在 `docs/runbooks/source-adaptation-matrix.csv` 中更新为 `[ ] 实测受阻` 并注明具体错误码与受阻原因，同时在 GitHub Issue Comment 中留痕说明。
3. **回归门禁必过**：每次批次提交前必须跑通 `uv run pytest`（含 `TestSourceConfigValidation` 与 `TestAdaptedSourceConfigReplayAndZeroTargetGuard`）、`uv run ruff check src tests` 与 `harness doctor`。
