# 归档读取与映射衔接设计及实施计划

日期：2026-11-16。沿用已批准的供应商采集、映射和样本验收范围，
用户要求继续开发。代码位于 `.worktrees/task27-research`；不提交或访问厂商服务。

**Goal:** 离线核验完整采集归档，按显式行路径生成携带实际接收时间的映射结果。
**Architecture:** 在现有归档校验器上增加只读 API，复用全部身份/摘要/授权核验；
新增独立桥接模块调用 MappingProfile 和 normalize_records。无网络、无数据库、
无归档修改，也不生成生产许可或自动声明分页完整。
**Tech Stack:** Python、Pydantic、stdlib、pytest、uv、Ruff。

继续手工提取行可用但缺少归档到映射的身份约束；直接固定 iFinD 响应结构
尚无真实字段依据。本轮选择显式路径配置的离线桥接，不引入厂商结构假设。
现有程序化调用和两类 CLI 保持原契约，本轮交付独立 API。

## 契约

- `supplier_collection.read_collection(request, grant=..., archive_root=..., clock=...,
  max_total_bytes=64*1024*1024)`：核验请求及全部已有档案/标记，
  仅当全部计划分块原始档案齐备时返回不可变 VerifiedCollection。
  原始档案完整但标记缺失可读取；不创建标记、发送请求或读取令牌。
  最多64 MiB归档文件总字节（含raw envelope和现有done标记）；
  超预算拒绝，不能缓存无限归档。
- 返回块同时携带不可变原始bytes和已有CollectedChunk元数据，
  原始数据不进入repr。使用首次校验的同一份bytes，避免二次读取换档。
- `supplier_archive_mapping.RowExtraction`：bar_path/state_path 为显式字符串键、
  整数数组索引的非空tuple；每条路径最多16段，至少声明一类。
  禁止bool/负索引、通配符、字段猜测和未知结构；所选值必须是对象数组。
- `normalize_collection(request, grant=..., archive_root=..., mapping_json=...,
  extraction=..., clock=...)`：有界严格mappingJSON（最多4MiB）及实际文件字节
  SHA256绑定请求四身份；先完整核验归档，再显式提取并调用normalize_records。
  提取行合计最多100000。缺路径/非法结构拒绝，不默默跳过分块。
- 两类profile均不得配置available_at字段映射或常量；桥接按每块实际
  first_received_at注入该字段。trade_date/limit_date/effective_from/
  effective_to/listing_date不得配置常量，日期须由供应商行显式提供或保持未知；
  结果provenance固定retrospective，PITunverified，production_approved=false。
- 保留normalization issues，不以成功行替代完整结果；结果携带采集指纹、
  profile摘要及不可变块元数据供后续样本/质量验收绑定。
- 库错误仅固定SupplierError代码；不泄漏raw、供应商列、路径或配置值。
  新固定码包括collection_incomplete、archive_budget_exceeded、
  invalid_extraction、extraction_failed、mapping_binding_mismatch。

## Task 1：只读完整归档

- [x] 新增tests/test_supplier_archive_mapping.py，离线collect生成4块，
  验证read_collection无请求/无写入、缺块、缺标记、篡改、过期授权、字节预算。
- [x] 运行pytest确认新API不存在导致失败。
- [x] 新增VerifiedChunk/VerifiedCollection及read_collection；
  `_preflight(..., retain=False, max_total_bytes=...)`只在只读路径保留已核验bytes，
  collect原路径保持流式校验；提取共享请求identity helper。
- [x] 重跑新测试及既有collection测试。

## Task 2：显式归档映射

- [x] 测试显式嵌套路径、精确数、receipt注入、四身份绑定、可用时间配置拒绝、
  缺路径/非法行/路径/过多行、问题保留及错误不泄漏。
- [x] 运行pytest确认桥接模块不存在导致失败。
- [x] 实现RowExtraction、不可变CollectionNormalization和normalize_collection；
  使用MappingProfile副本按每块注入时间，不修改调用者profile或归档。
- [x] 重跑测试；明确结果仍须样本质量验收，不自动设置complete_pages。

## Task 3：审查与交付

- [x] 一次独立只读集中审查，修复可证实问题并补反例。
- [x] 供应商全部回归、Ruff/format/compileall和Linux路径通过。
- [x] 更新使用文档、记录实际证据、重算源码并同步候选模板。
- [x] 保留以前验证事实，五项生产门禁仍blocked。

## 最终证据

初始31项测试失败；独立审查后新增3项反例先失败再通过。
最终新套件38 passed，供应商全套285 passed，Linux路径134 passed，
均0 skipped、0 warnings（`-W error`）。
全仓Ruff、329文件format check、compileall通过。
源码身份与候选模板一致；详细日志与边界见
[工程交付记录](supplier-ingestion-implementation.md) 的本轮追加记录。
