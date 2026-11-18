# Task 27 研究回测与本地模拟组合验收摘要

历史验收日期：2026-11-16，实现基线 `1adda8d`，对应当时研究工作区。
2026-11-17 主要功能已整合到本地 `main`。
本文件保留实现边界、验收摘要和可执行追溯；旧原始日志、截图与 trace 已清理，
历史原件可从 Git 查询。以下历史结果不能直接作为当前提交的全量验收。

## 已实现范围

- **B01**：PIT 回测、因子体检、walk-forward 和阶段条件调权。
  输入绑定快照、可见时间、publication_sequence 与配置摘要，信号最早次日成交。
  成交、成本和账本使用 Decimal/raw 价格；复权只用于声明的因子与收益口径。
  首版策略为 `factor-cross-sectional-topn-v1`。
- **B02**：IndexedDB v5 本地模拟账本，按 local_profile_id 分区；
  command 幂等、revision 乐观锁和单事务回读，订单不上传。
  AI 同池对照框架缺真实评测时固定 `disabled / real_evaluation_inputs_unavailable`。
- **EX14**：owner 专属 backtest POST，验证会话、CSRF、白名单、有界参数、
  幂等键与主体隔离；worker 租约支持排队、执行、取消与预算，失败不发布半成品。
- **EX15**：owner 专属结果/manifest GET 与取消 POST，结果已固化，GET 不重算。
  workbench/public 路由及默认构建不装配研究入口。

迁移 head 为 `0014_research`，parent 为 `0013_query`。
研究缓存不可变，降级仅限备份恢复流程。
真实生产输入与阶段序列仍需单独装配，详见 [生产输入契约](../production-inputs.md)。

## AC-1 至 AC-15 追溯

| 条目 | 可执行证据 |
| --- | --- |
| AC-1 防前视、AC-2 防幸存者偏差、AC-5 确定性与缓存 | [回测测试](../../tests/test_reset_backtest.py) |
| AC-3 交易约束、AC-4 T+1 与账本、AC-6 成本与拒单 | [交易规则与账本测试](../../tests/test_reset_research_validation.py) |
| AC-7 因子体检、AC-8 滚动验证、AC-11 AI disabled | [回测测试](../../tests/test_reset_backtest.py) |
| AC-9 接口权限、AC-10 生命周期 | [回测测试](../../tests/test_reset_backtest.py)、[全范围契约](../../tests/test_reset_full_scope.py)、[迁移测试](../../tests/test_release_migrations.py) |
| AC-12 本地账本、AC-13 双端规则 | [前端账本](../../web/tests/unit/reset-simulation.test.ts)、[面板](../../web/tests/unit/reset-simulation-panel.test.ts)、[后端规则](../../tests/test_reset_research_validation.py) |
| AC-14 界面与披露 | [研究 E2E](../../web/tests/e2e/reset-research.spec.ts) |
| AC-15 隔离与回归 | [全范围契约](../../tests/test_reset_full_scope.py)、[并发性能](../../tests/performance/test_reset_queries.py)、[验收编排](../../scripts/verify_reset.py) |

并发性能门使用真实研究 worker/内核与合成 100 证券、61 交易日输入，
在三个 20 并发公开 GET 轮次中验证活跃计算和 checkpoint 前进。
普通 GET 整体及各路径 P95 ≤2.0s；公开读零写，稳定数据表指纹一致。
验证器绑定原始产物 SHA-256，不接受自填通过状态。该门不证明真实策略有效性。

## 历史结果

| 门禁 | 2026-11-16 已登记结果 |
| --- | --- |
| 后端 | 4256 passed / 0 skipped，覆盖率 92% |
| 前端 | Vitest 1237、Node 28 passed；四种构建通过 |
| 浏览器 | 五套 E2E 合计 325 passed；研究 UI 另 4 例通过 |
| 性能 | 普通 GET P95 1.089s；并发研究三轮 1.075 / 1.037 / 1.159s |
| 平台与恢复 | TLS 10 例、Windows junction 2 例、指定容器恢复 1 例通过 |
| 静态与隔离 | Ruff/format/compileall、Docker API 契约和生产入口隔离通过 |

这些是历史本机开发验收记录。正式 `verify_reset.py --mode full` 的当前提交
受信报告与远程 CI 尚未提供，`automated_acceptance` 保持 `unverified`。

## 五项独立外部门禁

real_input、official_rules、real_model_evaluation、public_sharing_permit、
shadow_run_20_trading_days 均保持 **blocked**。
研究 worker 对缺少生产输入失败闭合；合成测试通过注入确定性输入运行真实内核。
真实数据和生产验收环境暂缓，见 [TODO](../TODO.md)，不构成生产发布许可。
