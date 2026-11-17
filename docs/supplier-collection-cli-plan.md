# 采集命令行入口设计与实施计划

日期：2026-11-16。用户再次明确要求继续开发；本轮沿用已批准的供应商接入范围、
已有 HTTP 采集及离线样本设计，补齐操作者命令行入口。
代码仍在 `.worktrees/task27-research`，不提交、推送、发布或使用真实供应商账号。

**Goal:** 通过私有请求、授权和映射配置调用已有采集器，支持安全预检及断点恢复。
**Architecture:** 新增薄 CLI，复用 CollectionRequest、MappingProfile、collect、
read_asset、strict_json 和 secure_output。采集/恢复逻辑仍由已有采集器负责。
**Tech Stack:** Python、argparse、Pydantic、stdlib、pytest、uv、Ruff。

比较程序化调用、CLI 和 Web 管理入口三种方式：保留程序化 API，
新增 CLI 便于本机执行；Web 管理不在批准范围内。
现有配置可直接接入，不引入厂商 SDK 或猜测厂商响应表结构。

## 明确契约

- 入口：`python -m dragon_radar.ingestion.collection_cli` 和
  `scripts/collect_supplier_sample.py`。
- 必须给出 `--request`、`--grant`、`--mapping`、`--archive-root`。
  前三个文件须私有、常规、非链接且有界；JSON 拒绝重复键/非有限数。
- 默认是配置预检，不发 HTTP、不写采集档案；`--execute` 才调用 collect。
  配置预检不宣称断点已经可恢复或字段已获厂商验证。
- request.profile_id/profile_sha256/source_id/source_version
  必须匹配实际 mapping 文件及 MappingProfile。
- 授权使用实际 UTC 时间；不提供回填接收时间的 `--now` 参数。
  预检也检查当前存储授权及归档根权限。
- 令牌只通过 `--token-env` 指定环境变量名；不得从参数/配置文件传令牌。
- timeout/max_attempts/retry_delay/request_interval 有界，预检即拒绝非法值。
- 可选 `--output` 必须位于归档目录外，拒绝覆盖、输入路径碰撞。
  输出文件无法创建时，在联网前失败。
- 报告 schema 为 `dragon-radar-supplier-collection-report/v1`，
  仅包含 mode、status、固定 code、配置摘要、证券/会话/分块计数、
  成功采集的 collection fingerprint。
  不输出来源名字、授权正文、供应商列名、请求/响应、证券或行情值。
- production_approved=false，historical_pit_status=unverified。
  退出0为预检/采集工程成功；2为授权/凭据/服务可用性缺项；1为配置、
  文件、映射身份、断点冲突、响应或输出错误。
- 验证只使用离线注入传输，不创建真实请求配置或生产授权文件。

## 实施

### Task 1：TDD 验证入口边界

文件：`tests/test_supplier_collection_cli.py`。

- [x] 写失败测试：默认预检0 sends，显式执行4 chunks，第二次执行0新增发送，
  实际 profile 文件摘要绑定、来源/版本/配置身份不匹配拒绝，授权到期及缺凭据，
  重复 JSON 键、非法参数、非私有输入和输出冲突拒绝，错误不泄漏植入值。
- [x] 执行
  `uv run --no-sync pytest tests/test_supplier_collection_cli.py -q -W error`，
  确认因入口尚未实现失败。

### Task 2：实现薄入口

文件：`src/dragon_radar/ingestion/collection_cli.py`、
`scripts/collect_supplier_sample.py`。

- [x] 实现私有 JSON 加载、实际 mapping 摘要绑定、UTC 存储授权检查、
  有界参数及归档根预检。
- [x] 用 secure_output 在执行前预留报告；执行委托已有 collect，
  固定错误码映射到安全 JSON 报告。
- [x] 重跑新测试；模块/脚本入口无数据库依赖，真实网络未调用。

### Task 3：复核、验证与文档

- [x] 集中独立只读审查及反例修复；不重复无实质变化的审查轮次。
- [x] 新入口及所有 supplier 套件通过，Ruff/format/compileall 通过。
- [x] Linux 入口/私有文件路径补验通过。
- [x] 更新使用指南；重算源码身份并同步候选模板，旧验证事实保留。
- [x] 本轮实际验证结果另存交付记录，五项生产准入仍 blocked。

## 实际结果

最初23项入口测试因模块/脚本不存在失败，日志为工作树
`.temp/supplier-collection-cli-red.log`。实现后23项通过；
补齐文件缺失、输出预留、后续断点冲突及错误码报告反例后35项通过。
缺失归档根反例先失败后修复；独立只读审查同时证实该问题。
完整供应商套件247 passed；Linux传输/归档/CLI 96 passed。
Ruff零问题、327文件格式检查和compileall通过。
详细证据见 [工程交付记录](supplier-ingestion-implementation.md) 的本轮追加记录。
