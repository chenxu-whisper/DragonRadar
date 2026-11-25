# 工程文档入口

2026-11-25 全需求剩余工程已整合到本地 `main`。
11 月 24 日冻结源码的 19 项本机工程门禁已通过，原始证据保持历史身份；
本次主工程验证与清理另行记录。
真实数据和生产验收环境暂缓；缺少许可或证明时，现有程序仍按契约关闭。

| 目的 | 文档 |
| --- | --- |
| 当前产品需求 | [开发需求 PRD](DragonRadar_开发需求PRD.md) |
| 本次全需求收口 | [2026-11-24 状态与设计入口](all-requirements-status-2026-11-24.md) |
| 当前主工程整合与清理 | [2026-11-25 主工程记录](acceptance/all-requirements-main-integration-2026-11-25.md) |
| 本次整合验证范围 | [源码冻结与最终门禁](acceptance/all-requirements-final-gates-2026-11-24.md) |
| 既有能力与历史条件对照 | [2026-11-17 需求进度](prd-development-progress-2026-11-17.md) |
| 前一阶段计划 | [2026-11-18 本机体验与工程验收](next-development-plan-2026-11-18.md) |
| C3 有限工程验收 | [AI 时间与实时契约验收报告](acceptance/contract-c3-2026-11-20.md) |
| C4 有限工程验收 | [五构建、性能与回归验收报告](acceptance/contract-c4-2026-11-20.md) |
| 后续待办 | [TODO](TODO.md) |
| 本机启动、所有者 TLS 与配对 | [统一 Reset 运行指南](reset-running.md) |
| API 与快照契约 | [工作台契约](contracts/workbench-api.md)、[来源契约](source-contracts.md) |
| 供应商采集与样本核验 | [使用指南](supplier-ingestion-guide.md)、[工程契约](supplier-ingestion.md) |
| 生产输入与独立证明 | [输入契约](production-inputs.md)、[证明契约](production-evidence.md) |
| 候选范围与证明配置 | [候选范围](production-candidate-scope.md)、[配置指南](proofs/README.md) |
| 数据导入、规则与模型 | [数据导入](data-import.md)、[监管规则](regulation-rules.md)、[AI 评估](ai-evaluation.md) |
| 数据库维护 | [备份恢复](operations/database-backup-restore.md) |
| 研究交付验证 | [Task 27 本机验收](acceptance/task27-research.md) |
| 历史全范围状态 | [历史终验及追加说明](acceptance/reset-final.md) |

`theory/` 与开发需求 PRD 完整保留。其余仅保留运行、接口、数据与生产证明契约、
需求进度、TODO，以及现有验收脚本和测试依赖的记录。
`acceptance/evidence/task20/` 和 `task21/` 为校验器依赖的原始证据，保持字节不变；
其他旧计划、重复说明、临时日志和研究截图/trace 已清理，历史内容可从 Git 查询。
本机数据、许可往来、个人研究媒体及待确认开发资产不进入代码提交。

唯一日常启动入口为 `.\scripts\start-reset.ps1`，根目录 `compose.yaml`
管理 Reset 七个服务；前端镜像与网关配置位于 `containers/`。
私人复盘运行说明已合入统一指南，不再维护重复入口。
`scripts/acceptance-stack.sh` 仅用于 CI 验收，不替代本机启动脚本。

`tests/` 核心 Python 回归覆盖主要业务、授权、发布、生产输入与证明、
迁移、性能及验收契约；本次新增指数历史、AI归档、分域时钟、样本比较、
分析容量和研究结果契约用例。共享 fixture 和 AI 评估清单仍为必需依赖。
Windows 启动与私有文件门禁位于 `scripts/test_reset_start_script.ps1`、
`scripts/test_private_scripts.ps1`，CI 继续执行。
早期分层与阶段复核测试已删除，历史测试数量不代表精简后套件的执行结果。
