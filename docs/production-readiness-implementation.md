# 生产外部门禁实施记录

日期：2026-11-16。依据：已批准的 `production-readiness-unblocking-design.md`。
代码工作树：`.worktrees/task27-research`，分支 `feature/task27-research`。
当前阶段：工程接入、规格复核、质量复核及相关验证已完成。
真实生产材料仍缺失，五项外部门禁保持 blocked。

## 工程交付

- 新增独立生产证明验证器，逐项报告五个门禁。外部策略、公钥摘要、
  RSA-PSS 签章、原始材料摘要、候选版本、范围、有效期和撤销均需核验。
- 新增受保护的供应商、首次接收记录、PIT 输入、已物化因子及快照血缘契约。
  本机 `analysis-worker --research-production-request` 显式装配既有研究内核，
  运行结果另存为不可覆盖的摘要命名归档。
- 规则核验重跑冻结样例并绑定官方归档、适用范围和最新生命周期。
  模型核验复用既有评估器，绑定真实输入输出及至少100个人工样本证明。
  公开许可按主体、用途、目标、受众、内容和数据精确核验。
- 影子采集追加原始记录；生产核验另外认证事先批准的完整交易日历和
  原始逐日签章，并逐日核对真实输入、实跑、接收、版本、审核和摘要链。
- 现有开发验收和发布审计仅增加显式接入。外部证明通过不升级
  `INTEGRITY_ONLY`，不授权发布，不覆盖既有 Task18 验收证据。

算子首版支持已批准 FAC-R01 的20/60/120日相对收益及 baseline。
未批准的其他因子、walk-forward 适配和 AI 研究腿继续拒绝；
签章通道本身不生成模型输出、人工标签或产品方法。
当前代码身份必须绑定已加载代码及完整源码、测试和构建输入。
仅安装 wheel 且缺少完整工作树的布局拒绝验证，不能用空目录摘要解锁。

详细操作契约：

- [生产证明](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/task27-research/docs/production-evidence.md)
- [生产输入与计算](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/task27-research/docs/production-inputs.md)

## 外部材料实际状态

用户根目录的 `data/suppliers.json` 与 `docs/proofs/` 已建立，
入口状态均为 present；供应商名册与证明索引仍为空。
候选及发布范围仅有待填写模板，尚无实际 `candidate.json` 和 `context.json`；
没有配置经外部确认的发行方信任策略。
开发工作树没有另设供应商资产；验证器通过 `--workspace` 读取用户根目录。
测试供应商、密钥和签名链只存在于隔离测试中，不是生产证明。

五项生产门禁因此仍为 blocked。真实授权数据、官方规则批准、
人工样本和模型输出、公开许可、20个原始交易日记录须由实际来源提供。
当前安全报告见 [production-readiness-status.json](file:///E:/ByteDance/Projects/Code/DragonRadar/docs/production-readiness-status.json)。

### 剩余四项复核

2026-11-16 再次检查用户根工作区、证明目录和开发工作树操作契约。
上传附件目录不存在，未取得新增外部材料。
四项门禁及签名链回归为 61 passed，0 skipped，0 warnings。
这些回归使用隔离测试材料，不计入生产证明。

| 门禁 | 当前缺项 | 实际解除所需 |
| --- | --- | --- |
| official_rules | 未确定实际候选规则版本/范围；无官方原文归档及独立批准 | 提供适用交易所、板块、证券状态和交易日；取得官方原文、规则定义、最新 active 生命周期、方法、冻结样例及独立签章 |
| real_model_evaluation | 无真实模型身份/输出、人工标签、评估清单和证明 | 提供匹配实际四版本的至少100个唯一人工样本、两个无泄漏分组、原始输入输出和独立证明；重新评估并通过全部阈值 |
| public_sharing_permit | 实际主体/用途/目标/受众/内容/数据尚未填写；无再分发许可 | 明确拟发布范围，取得对该范围明确允许再分发的有效独立许可 |
| shadow_run_20_trading_days | 无事先批准的日历、逐日真实接收/运行/复核及签章 | 接入获授权真实输入，按批准日历实际累计连续20个完成交易日；已有历史仅接受原始当日记录 |

共同前置条件是外部批准的信任策略、独立确认的策略 SHA256、
发行方公钥及适用批准身份。证明模板不建立信任。
`real_input` 也仍 blocked：空供应商名册不构成授权数据或实际运行证明。
四项不能通过修改报告、复制测试签章、助手代作人工标签或回填日志解除。
接入清单及命令见 `docs/proofs/README.md`。

本轮未暂存、提交、推送、合并、打标签、改变版本、发布镜像或部署。

## 验证记录

规格复核与质量复核均通过；发现的日历漂移、部署输入遗漏、结束阶段预算、
题材覆盖/分页、因子依赖可用时间、安装布局、畸形记录及总体失败状态均已修复。
对应反例先失败再通过；最终报告不复用旧源码 passing 摘要。

| 验证范围 | 实际结果 |
|---|---|
| Linux 完整相关回归：研究/回测/监管/owner/analysis、生产证明、现有验收器及 AI 评估器 | 939 passed，0 skipped，0 warnings |
| 最终 Linux 生产证明与输入套件，包含最终报告修复 | 235 passed，0 skipped，0 warnings |
| Windows 生产证明与输入全套 | 223 passed；后续身份/报告补强另行验证 |
| Windows 最终生产边界套件，包含签名链及畸形记录 | 63 passed，0 skipped，0 warnings |
| Windows 现有验证器/发布审计及 AI 评估器 | 238 passed，0 skipped；1个 POSIX 进程组探针明确 deselect，Linux 已覆盖 |
| 默认包隔离守卫回归 | 9 passed，0 skipped |
| 全仓库 Ruff / format / compileall | 0 Ruff 错误，312个文件格式通过，compileall 退出0 |
| sdist / wheel 构建及包内容 | 构建退出0；最终 wheel 源码与当前模块字节一致，无证明、公私钥或供应商资产 |
| 剩余四项验证器及签名链再复核 | 61 passed，0 skipped，0 warnings；使用 `-W error` |
| 指定根工作区的实际生产验证 | 退出2；5项 blocked，供应商/证明目录 present，信任策略 not_configured |

当前代码身份：
`source-sha256:c57c50b7edd2c7d293be59ca2ff0840607bd52aeea7ada81a0e6a6eb2ed06954`。

最终 wheel SHA256：
`59ca344ba24cd3d7822ec74c1b26b94ef2c6c2b8ce83d1a3b8790685c03a8a19`。
构建产物和运行日志保留在代码工作树 `.temp/`，未加入暂存区。

这些结果是本机工程验证，`automated_acceptance` 仍为 unverified，
`production_release` 仍为 blocked；不替代可信正式 CI 或发布授权。
