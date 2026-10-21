# Changelog

本项目的所有重要版本变更、新特性汇总与修复记录均归档于此。

## [Unreleased]

### Added
- 完成 `WorldLawHarvester` (`wlh`) v1 核心采集、CAS 归档、多模态解析（Office/TCVN3/PDF 扫描探测与 `br-ocr-v1` + `tesseract` 主备自动切换）、离线零外网 `reparse`、16 列标准模板投影与只读结果 API 实现及实网矩阵验收（Issue #1）。
- 建立 [`docs/runbooks/source-adaptation-matrix.md`](docs/runbooks/source-adaptation-matrix.md) 作为全球 574 个来源（`dd-001`–`dd-574`）的唯一事实源登记表，直接对齐 `资料类型` 与 `来源名称`。
- 初始化项目 Universal Harness 目录结构与文档规范。
