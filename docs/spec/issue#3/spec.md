# Fix: Wave 1 来源（`dd-017` / `dd-492` / `dd-493` / `dd-551`）跨页列表发现与 TOC 索引遍历补齐

> **Status:** Implemented · [Issue #3](https://github.com/nanzhi84/WorldLawHarvester/issues/3) · Created 2026-10-22
> **Affected Architecture:** [docs/architecture/overview.md](../../architecture/overview.md), [docs/architecture/canonical-legal-model.md](../../architecture/canonical-legal-model.md)

## 1. 背景与问题描述

在对全量已适配来源按 `budget.documents: 10` 进行实网采集与列表发现能力复查时，排查出 **Wave 1（[Issue #1](../issue#1/spec.md)）首批 8 个来源** 中有 4 个来源（[dd-017](../../runbooks/source-adaptation-matrix.md#dd-017)、[dd-492](../../runbooks/source-adaptation-matrix.md#dd-492)、[dd-493](../../runbooks/source-adaptation-matrix.md#dd-493)、[dd-551](../../runbooks/source-adaptation-matrix.md#dd-551)）存在跨页跟进受限或单篇 URL 硬编码问题，并联动 [Issue #2](../issue#2/spec.md) 对全量 39 个已落库来源建立反“单篇捷径（Reward Hacking）”契约门禁。

---

## 2. 修复方案明细

### 2.1 `dd-017`（最高人民法院指导案例 · [`sources/dd-017.yaml`](../../../sources/dd-017.yaml)）
- **原问题**：`allowed_path_prefixes` 写死了 `/shenpan/gengduo/77.html`（含 `.html` 后缀），导致 `/shenpan/gengduo/77_2.html` 等后续分页 URL 被 [`src/wlh/engine/scope.py`](../../../src/wlh/engine/scope.py) 拦截。
- **修复**：将 `allowed_path_prefixes` 中的 `/shenpan/gengduo/77.html` 改为 `/shenpan/gengduo/77`，并将 `budget.documents` 提升至 `10`，放行后续分页列表。

### 2.2 `dd-492`（越南政府文献门户 `vanban.chinhphu.vn` · [`sources/dd-492.yaml`](../../../sources/dd-492.yaml)）
- **原问题**：`adapter_options.max_pages` 设为 `1`，首页 10 条记录中有 2 条无原件附件被跳过（`detail_body: never`），单次运行仅产出 8 个资源；同时在 [`src/wlh/extraction/quality.py`](../../../src/wlh/extraction/quality.py) 中，纯扫描页（`suspected_scan: True`）Tesseract OCR 将数字 `8` 偶发误识为 `§` 时被误判为 `legacy_vietnamese_font_garbled`。
- **修复**：将 `adapter_options.max_pages` 提升至 `3`、`budget.documents: 10`，在 [`src/wlh/engine/runtime_registration.py`](../../../src/wlh/engine/runtime_registration.py) 中统计配额时排除 `failed`/`skipped` 目标，并在 [`src/wlh/extraction/quality.py`](../../../src/wlh/extraction/quality.py) 中对扫描页区分零星单字符 OCR 误识（如数字 `8` 偶发误识为 `§`）与高密度旧式越语 8-bit 字体乱码（单页乱码符号数 $\ge 8$ 或全篇 $\ge 16$ 且占比 $\ge 3\%$ 时仍触发 `legacy_vietnamese_font_garbled` 拦截）。

### 2.3 `dd-493`（越南电子公报 `Công báo Chính phủ` · [`sources/dd-493.yaml`](../../../sources/dd-493.yaml) & [`src/wlh/adapters/bridges/worldwidelaw_bridge.py`](../../../src/wlh/adapters/bridges/worldwidelaw_bridge.py)）
- **原问题**：`congbao.chinhphu.vn` 首页最新一期公报仅列出 5 部法令，且 `worldwidelaw_bridge.py` 在发现详情链接后丢弃了公报分类/往期列表导航链接。
- **修复**：在 [`src/wlh/adapters/bridges/worldwidelaw_bridge.py`](../../../src/wlh/adapters/bridges/worldwidelaw_bridge.py) 中保留受控列表导航链接跟进（严格过滤掉带 `detail_url_patterns` 约束下的非详情页误入库），并在 [`sources/dd-493.yaml`](../../../sources/dd-493.yaml) 中设置 `max_depth: 2`、`budget.documents: 10`。

### 2.4 `dd-551`（德国联邦最高法院判例库 `Rechtsprechung im Internet` · [`sources/dd-551.yaml`](../../../sources/dd-551.yaml) & [`src/wlh/adapters/bridges/oldp_bridge.py`](../../../src/wlh/adapters/bridges/oldp_bridge.py)）
- **原问题**：`sources/dd-551.yaml` 的 `entry_urls` 直接硬编码了单篇裁判 ZIP 直链（`jb-JURE100055033.zip`）。
- **修复**：在 [`src/wlh/adapters/bridges/oldp_bridge.py`](../../../src/wlh/adapters/bridges/oldp_bridge.py) 中支持解析德国官方全量 XML 索引目录（`bsjrs-toc.xml`，忽略 `<items>` 索引根节点并在 `next_requests()` 中派生子级 `.zip` 请求），并将 [`sources/dd-551.yaml`](../../../sources/dd-551.yaml) 的 `entry_urls` 切换至 `https://www.rechtsprechung-im-internet.de/jportal/docs/bsjrs/bsjrs-toc.xml`（`max_depth: 1`、`budget.documents: 10`）。

### 2.5 反“单篇捷径（Reward Hacking）”长效门禁
- 在 [`tests/test_storage_and_config.py`](../../../tests/test_storage_and_config.py) 新增 `test_collection_sources_prohibit_single_document_reward_hacking_shortcuts`，对全量集合型来源强制校验：`fetch.max_depth >= 1`、`budget.documents >= 10`、禁止 `attachment_url_pattern: ^\b$`、禁止将 `attachment_url_pattern` 锁死在单篇文档文件名、禁止 `entry_urls` 直接命中 `detail_url_patterns`、禁止在 `allowed_path_prefixes` 写入阻塞分页的静态后缀、禁止 `max_pages < 2`。
- 在 [`tests/test_source_adapters.py`](../../../tests/test_source_adapters.py) 中对所有修复后的多篇列表/TOC 来源强制断言单次 `collect` 至少从列表/索引页派生并入库 $\ge 2$ 个独立 `wlh.resource`。

---

## 3. 验收 Checklist

- [x] 修复 `sources/dd-017.yaml` 的 `allowed_path_prefixes`，验证跨页列表 URL 可正常通过 `scope.guard` 并跟进。
- [x] 修复 `sources/dd-492.yaml` 的 `adapter_options.max_pages`，实网 `collect -> reparse` 验证拉满 `10` 个资源。
- [x] 修复 `src/wlh/adapters/bridges/worldwidelaw_bridge.py` 与 `sources/dd-493.yaml` 列表跟进逻辑，实网 `collect -> reparse` 验证拉满 `10` 个资源。
- [x] 修复 `src/wlh/adapters/bridges/oldp_bridge.py` 与 `sources/dd-551.yaml` 支持官方 `bsjrs-toc.xml` 索引发现，实网 `collect -> reparse` 验证拉满 `10` 个资源。
- [x] 通过 `uv run pytest`、`uv run ruff check src tests` 与 `harness doctor`。
