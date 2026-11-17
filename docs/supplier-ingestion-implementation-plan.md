# 供应商接入实施计划

> 执行方式：子代理负责独立采集与映射任务，主代理负责验收 CLI、集成、规格复核和质量验证。
> 用户已要求开始实施，不重复请求批准。禁止创建提交、推送、生产配置或许可。

**Goal:** 完成 HTTP 采集、可配置 canonical 映射和离线样本验收链路。
**Architecture:** 传输和映射独立，验收复用现有 domain 质量门禁。
**Tech Stack:** Python 3.12、stdlib urllib、Pydantic、pytest、uv、Ruff。
**设计依据:** `docs/supplier-ingestion-design.md`。

## Task 1：HTTP 采集与恢复

- [x] 创建 `tests/test_supplier_collection.py` 和 `tests/test_supplier_transport.py`；
  用离线传输及显式时钟验证分批、授权到期、429/5xx、重试耗尽和归档篡改。
- [x] 执行 `uv run --no-sync pytest tests/test_supplier_collection.py tests/test_supplier_transport.py -q -W error`，记录预期失败。
- [x] 实现 `src/dragon_radar/ingestion/supplier_transport.py` 与
  `supplier_collection.py`；输出与异常只使用安全错误码。
- [x] 重跑测试，通过后复核许可检查时序、恢复身份和文件权限。

## Task 2：字段映射

- [x] 创建 `tests/test_supplier_mapping.py`，先验证显式映射、未知枚举、
  浮点拒绝、精确倍率、缺字段、稳定身份和 profile/source 约束。
- [x] 执行 `uv run --no-sync pytest tests/test_supplier_mapping.py -q -W error`，记录预期失败。
- [x] 实现 `src/dragon_radar/ingestion/supplier_mapping.py` 的
  MappingProfile、NormalizationResult 和 normalize_records。
- [x] 重跑测试，通过后复核所有拒绝路径都不打印原始行或值。

## Task 3：样本验收与命令行

- [x] 创建 `tests/test_supplier_sample.py` 与 `tests/test_supplier_sample_cli.py`；
  验证合法21会话、缺状态/限价/基准、错误单位、重复记录、范围及脱敏错误。
- [x] 执行 `uv run --no-sync pytest tests/test_supplier_sample.py tests/test_supplier_sample_cli.py -q -W error`，记录预期失败。
- [x] 实现 `src/dragon_radar/ingestion/sample_acceptance.py`、
  `sample_cli.py` 与 `scripts/verify_supplier_sample.py`。
- [x] 提供模块和脚本离线入口，验证无数据库配置也能运行，
  映射失败输出安全报告且退出非零，输出文件拒绝覆盖。
- [x] 在代码工作树 `docs/supplier-ingestion.md` 写清输入格式和运行命令；
  fixture 仅保留于测试，不生成生产供应商配置。

## Task 4：集成与最终验证

- [x] 规格逐项检查任务1–3，集中修复发现的问题。
- [x] 运行新增全套及现有 production/input/ingestion 相关核心回归。
- [x] `uv run --no-sync ruff check .` 与 `ruff format --check .` 零错误。
- [x] `uv run --no-sync python -m compileall -q src scripts tests` 退出0。
- [x] CLI 离线演示：合格测试样本退出0且 test_only/production_approved 明确；
  缺证样本退出2；畸形 JSON 退出1且不泄漏植入的凭据。
- [x] 重算源码身份，同步模板并另存工程验证记录；
  五项外部门禁继续 blocked，原验证日志保留历史身份。

最终：新增212 passed，离线核心958 passed、159项缺测试数据库明确 deselect；
两套均0 skipped、0 warnings。全仓 Ruff/324文件 format/compileall 通过。
完整证据与数据库验证边界见 `docs/supplier-ingestion-implementation.md`。

## 用户要求继续后的补验

- [x] 核验本机既有专用 `dragon_test` 数据库，仅使用临时 schema。
- [x] 单独执行原159项数据库集成测试：159 passed，0 skipped，0 warnings。
- [x] 比对运行前后 schema，11个既有测试 schema 保留，本次临时 schema 已清理。
- [x] 同步交付记录：累计 Windows 1329项测试通过，生产准入状态保持 blocked。
- [x] 补验 Linux 传输/归档恢复路径：61 passed，0 skipped，0 warnings；临时容器自动清理。
