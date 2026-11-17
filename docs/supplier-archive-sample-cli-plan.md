# 离线归档样本 CLI 设计与实施计划

日期：2026-11-16。沿用用户已批准的采集、映射、质量与样本 CLI 范围。
工作树 `.worktrees/task27-research`；不暂存、提交或访问真实供应商服务。

**Goal:** 提供一个完整归档核验、显式映射、样本质量验收的本机离线入口。
**Architecture:** 独立 `archive_sample_cli` 读取六项显式配置，复用
`normalize_collection` 和 `evaluate_sample`；薄脚本包装，不接数据库或网络。
**Tech Stack:** Python、Pydantic、pytest、uv、Ruff。

现有手工行文件 CLI 无法关联原始归档；直接自动生成样本上下文会推定
日历、授权和分页完整性。本轮选择显式空样本模板，保留操作者声明的上下文。

## 契约

- 必需 `--request --grant --mapping --extraction --sample --archive-root`；
  可选 `--output --now`。无联网开关，不构造传输、不读取token。
- 五项配置均使用私有有界 `read_asset`，上限4 MiB、严格JSON。
  请求和路径通过 `model_validate_json` 保留严格日期/tuple语义。
- sample沿用SampleRequest，batch的bars/security_states/limit_up_events必须为空，
  provenance必须retrospective。不得从旧列表回退，也不得添加未经归档的事件。
- sample来源及版本、完整grant、完整calendar与采集配置相等；
  batch.calendar_version与calendar.version相等，trade_date为采集最后会话。
  canonical股票池由sample显式声明；不猜测供应商代码与canonical ID的转换。
- 映射使用实际字节SHA及四身份绑定；全部归档齐备才提取。
  所有映射问题导致fail并保留安全问题列表，不调用质量验收。
- batch.as_of由sample明确给出，不自动改写；若早于任一实际receipt则fail。
  bars不得超出采集sessions；其余版本、池、事实、覆盖、基准由样本质量检查。
  保留每行实际available_at；报告固定retrospective/PITunverified/productionfalse。
- complete_pages保留sample显式声明；完整归档不能自动证明供应商分页完整。
- 报告仅安全摘要、输入摘要、collection指纹、块数和质量问题；不输出原始行、
  供应商字段、路径、授权reference或异常值。0/2/1分别pass/blocked/fail。
- 输出必须在归档目录外且不等于任何输入；独占私有预留后才读取归档；
  输出预留失败不得开始归档处理。stdout与文件报告一致。

## 实施

- [x] 新整链测试：真实离线归档到21会话合格样本、质量缺项、分页声明、
  接收时间、混入记录、身份/授权/日历、问题保留、完整性、路径与输出安全。
- [x] 观测新入口不存在造成的RED，再实现独立模块和薄脚本，观测GREEN。
- [x] 一次集中独立只读审查，补充证实问题的反例并统一修复。
- [x] 供应商全套、Linux路径、全仓Ruff/format/compileall验证。
- [x] 同步使用文档、源码身份与候选模板，追加实际证据，生产五门禁保持blocked。

初始37项失败，其中36项因新入口不存在，另一项fixture误用raw文件名。
实现后36项通过，fixture仍失败，
改为读取CollectedChunk.raw_path。新增默认时钟授权到期反例先失败（实际返回0），
再修复为向归档传递动态UTC时钟，并于最终验收重取操作时间；
`--now`仍为显式固定重放时间。最终新入口38 passed，0 skipped、0 warnings。
独立只读集中审查未发现可证实问题，语法与错误输出复核通过。
最终Windows和Linux各323 passed，0 skipped、0 warnings（`-W error`），
全仓Ruff、332文件format check及compileall通过。
候选源码身份同步，五项生产门禁仍blocked；完整证据见
[工程交付记录](supplier-ingestion-implementation.md) 的最新追加记录。
