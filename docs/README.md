# 工程文档入口

主要功能已整合到本地 `main`，从项目根目录开发。
真实数据和生产验收环境暂缓；缺少许可或证明时，现有程序仍按契约关闭。

| 目的 | 文档 |
| --- | --- |
| 当前产品需求 | [开发需求 PRD](DragonRadar_开发需求PRD.md) |
| 已实现能力与剩余条件 | [需求进度对照](prd-development-progress-2026-11-17.md) |
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

`tests/` 精简为 48 个核心 Python 测试文件，覆盖主要业务、授权、发布、
生产输入与证明、迁移、性能及验收契约；共享 fixture 和 AI 评估清单仍为必需依赖。
Windows 启动与私有文件门禁位于 `scripts/test_reset_start_script.ps1`、
`scripts/test_private_scripts.ps1`，CI 继续执行。
早期分层与阶段复核测试已删除，历史测试数量不代表精简后套件的执行结果。
