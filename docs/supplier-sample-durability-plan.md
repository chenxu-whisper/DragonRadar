# N1C：标准样本持久化授权边界收尾

日期：2026-11-17。用户要求继续开发；本切片修复已有授权存储契约的遗漏。
状态：已完成；Windows/Linux各412项供应商回归通过，独立复核无重要缺陷。
完整证据见 [N1C交付记录](supplier-ingestion-implementation.md#n1c标准样本持久化授权边界收尾)。

**Goal:** 标准样本保存API及整链CLI在最终flush/fsync后、文件仍可回滚时复核当前授权。

**Architecture:** 复用N1B已有的 `secure_output(on_durable=...)`。
既有写入前后授权检查继续保留；共享回调只补上持久化完成边界。
采集器已有fsync后的before_publish检查，不修改采集协议。

**Tech Stack:** Python、pytest、uv、Ruff；无新增依赖或CLI参数。

工作树为 `.worktrees/task27-research`。本轮不需要真实供应商材料，
不改变schema、摘要范围、接收时间、质量门槛或生产准入规则。

## 实施契约

- `write_sample_archive`增加持久化完成授权回调。期间过期抛固定
  `SampleArchiveError("storage_denied")`，清理新建文件，不返回摘要。
- `archive_sample_cli._publish`为样本和报告输出绑定同一回调，
  只在结果包含已合格archive时复核其grant与source；不重复质量计算。
- report-only、sample-only、两输出模式都必须拒绝持久化完成时已过期的pass。
  对CLI返回安全blocked JSON和退出2。
- 两输出仍先预留，再读输入；报告先完成，随后发布样本。
  报告fsync期间失效清理两个输出。样本fsync期间失效清理样本，
  已完成的先行报告可保留；报告继续不是样本完成标记。
- 不增加手工unlink、宽松权限或新时钟选项；仍由安全输出作用域负责回滚。
- 保留既有 `_NoArchive`：质量blocked/fail不发布样本，失败诊断可保存。
- API、CLI、私有文件及错误契约兼容；所有操作保持离线。

## 文件与步骤

1. 在 `tests/test_supplier_sample_archive.py` 加API fsync期间授权失效反例；
   在 `tests/test_supplier_archive_sample_cli.py` 加report-only、sample-only、
   两输出报告fsync及样本fsync四个反例；先实际观察RED。
2. 最小调整 `src/dragon_radar/ingestion/sample_archive.py` 和
   `src/dragon_radar/ingestion/archive_sample_cli.py` 的安全输出回调；
   更新已有注入输出包装，使其透明转发回调参数。验证GREEN和旧输出行为。
3. 集中一次独立只读复核；统一处理有证据的问题。
4. Windows/Linux供应商回归、Ruff/format、compileall及wheel实际入口；
   同步使用文档、交付记录、路线图和最终源码身份。

日志、构建和缓存位于项目外 `E:\ByteDance\Projects\Code\.temp\DragonRadar-N1C`。
保护既有未提交修改，不暂存、提交、推送、合并、修改版本、发布、部署、生产迁移、
调用真实供应商或发送邮件。生产五门禁按真实缺项保持blocked。
