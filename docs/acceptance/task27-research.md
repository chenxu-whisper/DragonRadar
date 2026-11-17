# Task 27 研究回测与本地模拟组合验收

记录日期：2026-11-16。执行工作树：`.worktrees/task27-research`，
分支：`feature/task27-research`，实现基线：`1adda8d`。
本次 Task 18 改动尚未提交，验证结果须对应实际工作区。

**当前状态：已实现范围的开发验收完成。** B01/B02 与 EX14/EX15 已有实现和自动化证据；
本次本地门禁均已通过，结果在本文件逐项登记。合成研究能力不代表真实策略有效性，
五项外部生产门禁保持 blocked。

## B01

PIT 回测、因子体检、walk-forward 滚动验证与五阶段条件调权已实现。
输入绑定已发布快照、可见时间、publication_sequence 与配置摘要；信号最早
在下一交易时点成交。成交、成本和账本采用 Decimal，价格为 raw，
复权仅用于已声明的因子/收益口径。首版策略为
`factor-cross-sectional-topn-v1`，不扩展为任意策略 DSL。

证据：[回测测试](../../tests/test_reset_backtest.py)、
[交易规则与账本测试](../../tests/test_reset_research_validation.py)、
[研究界面 E2E](../../web/tests/e2e/reset-research.spec.ts)。

## B02

浏览器 IndexedDB v5 的 `simulation_accounts / simulation_orders /
simulation_fills / simulation_commands` 按 local_profile_id 分区。
下单采用 command 幂等、revision 乐观锁、单事务提交与回读校验，
重开后恢复。前后端交易规则通过共同黄金样例对账；模拟组合不上传订单。

AI 增益对照仅交付同池、同窗、同成本、同种子的实验框架。
缺少真实冻结评测材料时，ai_variant 固定
`disabled / real_evaluation_inputs_unavailable`，无该腿收益数字。

证据：[本地账本测试](../../web/tests/unit/reset-simulation.test.ts)、
[模拟面板测试](../../web/tests/unit/reset-simulation-panel.test.ts)、
[研究界面 E2E](../../web/tests/e2e/reset-research.spec.ts)。

## EX14

`POST /api/research/backtests` 仅在 owner 应用装配：
会话、CSRF、输入白名单、有界参数、幂等键与主体隔离均须验证。
执行使用现有 worker 进程与租约机制，支持 queued / running /
succeeded / failed / cancelled；失败不得发布半成品。

## EX15

`GET /api/research/backtests/{run_id}` 读取固化结果，不在 GET 时重算。
owner 只能读取本人 run。结果携带 config_hash、输入版本、指标、成交、
拒单、净值、折结果、成本敏感性、体检和 disabled 对照说明。
额外 manifest GET 与 cancel POST 同样仅在 owner 入口开放。
workbench/public 路由及默认构建不得泄漏研究入口。

迁移 head 为 `0014_research`，parent 为 `0013_query`；
研究缓存不可变，降级仅允许备份恢复流程。

## AC-1 至 AC-15 追溯

| 条目 | 具体证据 |
|---|---|
| AC-1 PIT 防前视 | [未来修订不改变锚定窗口](../../tests/test_reset_backtest.py#L781)、[迟到行情排除](../../tests/test_reset_backtest.py#L754) |
| AC-2 防幸存者偏差 | [逐日 PIT 池](../../tests/test_reset_backtest.py#L701)、[退市证券保留](../../tests/test_reset_backtest.py#L733)、[窗口后上市不可见](../../tests/test_reset_backtest.py#L762) |
| AC-3 交易约束 | [九类拒单](../../tests/test_reset_research_validation.py#L171)、[最早次日成交](../../tests/test_reset_research_validation.py#L220) |
| AC-4 T+1 与非负账本 | [T+1](../../tests/test_reset_research_validation.py#L308)、[账本守恒与非负](../../tests/test_reset_research_validation.py#L519) |
| AC-5 确定性与缓存 | [跨进程字节一致](../../tests/test_reset_backtest.py#L1555)、[跨主体缓存复用](../../tests/test_reset_backtest.py#L3301) |
| AC-6 成本与拒单比例 | [成本敏感性](../../tests/test_reset_research_validation.py#L628)、[指标黄金样例及双口径拒单比例](../../tests/test_reset_research_validation.py#L558) |
| AC-7 因子体检 | [覆盖率分区守恒](../../tests/test_reset_backtest.py#L1657)、[含 ties 的 Spearman](../../tests/test_reset_backtest.py#L1754) |
| AC-8 滚动验证 | [折分区](../../tests/test_reset_backtest.py#L2117)、[测试区不改变拟合权重](../../tests/test_reset_backtest.py#L2177)、[阶段输入不读未来](../../tests/test_reset_backtest.py#L2208) |
| AC-9 接口与权限 | [CSRF](../../tests/test_reset_backtest.py#L3840)、[主体隔离](../../tests/test_reset_backtest.py#L4095)、[全范围路由契约](../../tests/test_reset_full_scope.py) |
| AC-10 生命周期 | [协作取消](../../tests/test_reset_backtest.py#L4813)、[运行预算](../../tests/test_reset_backtest.py#L4847)、[真实 PostgreSQL 迁移](../../tests/test_release_migrations.py) |
| AC-11 AI disabled | [无评测输入无收益数字](../../tests/test_reset_backtest.py#L2341)、[各腿共同输入](../../tests/test_reset_backtest.py#L2371) |
| AC-12 本地账本 | [账本单测](../../web/tests/unit/reset-simulation.test.ts)、[面板单测](../../web/tests/unit/reset-simulation-panel.test.ts)、[恢复与零网络 E2E](../../web/tests/e2e/reset-research.spec.ts) |
| AC-13 双端规则对账 | [买入黄金样例](../../tests/test_reset_research_validation.py#L377)、[卖出黄金样例](../../tests/test_reset_research_validation.py#L396)、[前端黄金样例](../../web/tests/unit/reset-simulation.test.ts) |
| AC-14 界面与披露 | [交互 E2E](../../web/tests/e2e/reset-research.spec.ts)、[运行日志](evidence/task27/research-ui-final.log)、[trace 与截图索引](evidence/task27/manifest.json)：提交、轮询、取消、结果表、375/1440px、200% 缩放、console 零错误 |
| AC-15 隔离与回归 | [构建与回归命令](evidence/task27/commands.json)、[完整源码与产物摘要](evidence/task27/manifest.json)、[并发性能门](../../tests/performance/test_reset_queries.py)、[独立复核](evidence/task27/implementation-review.md) |

前端 rubric 与工程 rubric 的评分登记在下文，依据本次实际运行与独立审查。

## Task 18 concurrent load gate

现有 performance 门新增合成研究负载：真实
`ResearchRunRepository → ResearchBacktestWorker → run_backtest`，
100 个证券、61 个交易日、确定性日线/因子投影。
在三个固定 20 并发公开 GET 轮次结束前持续执行真实引擎，
每轮必须记录 worker 活跃和 checkpoint 前进；完成后固化结果并核对摘要。
这只检验研究计算与公开查询的资源竞争，不证明生产因子装配或策略有效性。

普通 GET 整体及各路径 P95 ≤ 2.0s，保留原有公开读零写入和数据库指纹检查。
`verify_reset.py` 强制验证并发研究字段，并把其与带 SHA-256 的原始性能
产物绑定；缺失并发记录、无进度、失败状态、摘要不匹配、超预算均不得通过。
既有 CI 和 full/focused 编排已调用性能测试文件，因此复用原门禁。

证据：[性能测试](../../tests/performance/test_reset_queries.py)、
[验收编排器](../../scripts/verify_reset.py)、
[验证器负向测试](../../tests/test_verify_reset.py)。

## 本次运行结果

最终日志、原始性能数据、覆盖率与 UI 证据已归档到
[证据目录](evidence/task27/manifest.json)，原始执行目录为
`C:\Users\Admin\AppData\Local\Temp\DragonRadar-task18-20261116`。
下列结果均在对应命令结束后登记。

| 门禁 | 状态 |
|---|---|
| PostgreSQL 并发性能及公开零写 | 最终 1 passed / 0 skipped，EXIT=0；普通 GET 整体 P95 1.089s；真实研究计算下三轮各 20 请求 P95 为 1.075 / 1.037 / 1.159s，均 ≤ 2.0s；公开请求零写，除研究 run/cache 两表外指纹一致，原始证据验证器零错误 |
| PRD 与全范围追溯 | PRD 契约 EXIT=0：57 REQ / 29 endpoints / 29 examples / 30 JSON；最新 PostgreSQL 全范围与验证器合计 121 passed / 0 skipped，EXIT=0 |
| 后端全量与覆盖率 | 4256 passed / 0 skipped，四个完整文件分片分别 1064 / 1065 / 1064 / 1063 passed，均 EXIT=0；合并覆盖率 92%；验收容器补齐 Docker Compose、真实 HEAD 离线元数据及 `--init` 子进程回收 |
| Ruff lint / format / compileall | 最新检查均 EXIT=0；292 个 Python 文件 format 检查通过 |
| 前端单测与类型 | 最终 npm test EXIT=0：Vitest 1237 / Node 28 passed，0 failed / 0 skipped；此前重负载单例 timeout 已在原 5 秒限制下复测通过；四构建均含 tsc --noEmit |
| 默认、私有、reset、sharing 构建 | 四构建修复后均 EXIT=0；默认 bundle 307976 ≤ 315500，sharing 全产物与体积门 319955 ≤ 320000 bytes |
| legacy / reset / private / AI E2E | 五套均 EXIT=0：81 / 170 / 69 / 1 / 4 passed，共 325 passed / 0 skipped；私人会话检查测试改为等待首次权威读取完成后记录请求基线 |
| AC-14 研究界面证据 | 4 passed / 0 skipped，EXIT=0；375px、1440px / 200%、提交→轮询→完成、取消、本地账本恢复与 console 零错误；留存四份 trace 与页面截图 |
| Docker 生产入口隔离 | 修复后 EXIT=0，status=passed；private 32772 / public 32773 |
| Docker API 契约运行时 | 修复后 EXIT=0，status=passed；owner HTTPS 32774 / public 32775 |
| 真实私有 TLS 网关 | 10 passed / 0 skipped，EXIT=0；测试栈已清理，未安装系统信任证书 |
| 真实指定容器备份恢复 | 1 passed / 0 skipped，EXIT=0；源未变、零数据损失、权限保留、演练资源清理；临时容器与网络已清理 |
| Windows 平台补充探针 | junction 2 passed / 0 skipped，EXIT=0；私有文件 ACL 与 reset 首次运行脚本检查均 EXIT=0；Linux collection 中两个 Windows 探针与指定恢复探针仅因运行环境不适用而 deselect，均已在对应环境实跑 |
| 规格审查与质量审查 | 规格 PASS，质量 APPROVED；[复核记录](evidence/task27/implementation-review.md)，实际运行结果由本次门禁日志单独证明 |

## 最终证据与评分

[命令与退出码](evidence/task27/commands.json)、
[源码与产物 SHA-256](evidence/task27/manifest.json)、
[合并覆盖率](evidence/task27/linux-coverage-final.xml)、
[性能验证记录](evidence/task27/performance-validation-final.log)
绑定现有 HEAD 与实际未提交工作区；manifest 包含全部已跟踪及未跟踪源码的摘要，
并逐项校验测试日志、性能原始 JSON、四份 trace 与页面截图。

Linux 全量 4256 项、真实 TLS 10 项、Windows junction 2 项、
指定容器恢复 1 项和专用性能 1 项均实际通过，全部执行结果为 0 skipped。
前端 Vitest 1237 项与 Node 28 项、五套浏览器测试合计 325 项通过；
额外研究 UI 四例复测留存 trace 和整页截图。
依赖与运行时容器配置的
[diff 为空](evidence/task27/dependency-diff-final.log)，`web/dist` 已按原约定清理。

| Rubric | 评分 / 门槛 | 依据 |
|---|---|---|
| 前端交互与披露（AC-14） | 4/5，≥4 | 提交与 manifest 闸门、轮询终态与取消、本地恢复、研究披露、375/1440px 与 200% 缩放、console 零错误均实测通过；trace 与截图可核对 |
| 工程隔离与零回归（TR-18.3 / AC-15） | 4/5，≥4 | 独立规格 PASS / 质量 APPROVED；全量与平台补充门禁通过，default/sharing 全资产隔离扫描与体积门通过，并发性能、公开零写与稳定表指纹通过，依赖及运行时配置无变更 |

这些结果是本地 CI 等价命令与合成回归证据。
未生成正式 `verify_reset.py --mode full` 的受信执行报告，也没有远程 CI
运行记录；当前未提交工作区的验证由 HEAD 加源码摘要精确绑定。
评分仅针对开发范围，不构成真实策略评测或生产发布许可。

## 五项独立外部门禁

| 门禁 | 状态与原因 |
|---|---|
| real_input | blocked：没有真实获许可的冻结行情/新闻输入 |
| official_rules | blocked：没有已批准的正式规则与复核材料 |
| real_model_evaluation | blocked：没有 ≥100 条人工标注真实评测样本及事实支持率验证 |
| public_sharing_permit | blocked：没有公开发布许可 |
| shadow_run_20_trading_days | blocked：没有至少 20 个交易日的真实依赖逐日记录 |

研究 worker 的默认计算装配仍对缺少生产因子物化输入失败闭合；
合成测试通过注入确定性输入运行真实内核。此边界必须在实际部署前解决。
本次开发验收不构成生产发布验收。
