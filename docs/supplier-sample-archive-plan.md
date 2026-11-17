# N1 不可变标准样本归档实施规格

**Goal:** 将已通过离线映射和质量验收的完整标准样本保存为可重新核验的私有归档。

**Architecture:** 提取 `archive_sample.py` 共享服务，返回安全报告和仅在 pass 时存在的样本包。
`sample_archive.py` 负责契约、canonical 字节、私有独占写入及按外部预期摘要读取。
CLI 默认行为不变；显式 `--sample-output` 启用归档。沿用已批准 N1 范围，直接在现有工作树实施。

**Tech Stack:** Python、Pydantic、pytest、uv、Ruff。无网络、数据库或生产入口。

## 固定契约

- schema：`dragon-radar-normalized-sample-archive/v1`。
- 顶层字段：schema、sample、lineage、production_approved=false、
  historical_pit_status=unverified。sample 是完整 `SampleRequest`，保持精确 Decimal、
  顺序、日历、授权、as_of、available_at、complete_pages 和 retrospective provenance。
- lineage：五份原始配置字节的 SHA256（request/grant/profile/sample_file/extraction）、
  collection_fingerprint、chunks。每块仅保留 index、fingerprint、raw_sha256、
  archive_sha256、first_received_at；不保存路径、原始响应、令牌或完整授权证明。
- SHA256 为完整 canonical UTF-8 文件字节摘要，不嵌入文件自身；报告新增
  sample_archive_sha256。读取必须提供来自调用方保存的 expected_sha256，
  文件自称的摘要不构成信任。该摘要保证本机完整性，不证明供应商真实性。
- 最大文件 16 MiB；最多10000块；索引必须从0连续，每块摘要为小写64位hex。
  每条行情/状态实际接收时间必须属于块接收时间集合，所有块接收时间不得晚于as_of。
- 创建及读取均校验全部 schema、质量和当前操作时间的存储授权。
  读取额外要求调用方当前 SourceGrant 与样本授权完全一致；授权内容变更需要重新归档。
  在读取前及返回前再次授权，禁止过期时返回样本；不自动授予展示/导出/分享/AI权利。
- 拒绝未知字段、重复键、非finite JSON、非canonical字节、摘要不符、链接、非私有文件、
  超预算、身份漂移、不合格质量及非retrospective provenance。
- 这是工程标准样本，不能标为历史PIT验证或生产批准。

## 多产物发布与失败

所有输出先做路径冲突检查（互相、输入文件、采集归档目录），再独占预留，
然后才读取任何配置和采集内容。样本文件最外层预留为空；诊断报告内层写入并完成
flush/fsync/关闭后，重新授权、序列化并写样本，最后 flush/fsync/关闭样本。
只有有完整canonical字节且与调用方预期摘要一致的文件才可消费。
blocked/fail、报告写入失败、样本序列化或写入/fsync失败均通过异常清理样本预留。
进程被强制终止可能留下空/部分文件；读取拒绝这些文件，重跑拒绝覆盖，人工核对后清理。
报告不是完成标记；成功消费以样本文件完整性和当前授权核验为准。

## 实施步骤与验收命令

- [x] 新增 `tests/test_supplier_sample_archive.py`，复用现有私有root及离线采集fixtures；
  先用缺失 `archive_sample`/`sample_archive` 模块实际运行并观察RED。
- [x] 从CLI提取共享服务；保留所有原报告字段与错误码，坏映射不调用质量验收。
  服务只在pass时构造样本包，禁止blocked/fail返回可持久化对象。
- [x] 实现严格包模型及 `archive_bytes`、`write_sample_archive`、
  `read_sample_archive(path, expected_sha256, grant, clock)`；
  测试往返、精确值、摘要、篡改、授权、权限、预算及失败无包。
- [x] CLI增加 `--sample-output`，测试既有报告行为、两输出一致、预留顺序、
  既有目标、路径冲突、缺历史、坏映射、动态授权和写入/fsync失败。
- [x] 一次集中独立只读复核，统一处理可证实反例。
- [x] 执行 `uv run --no-sync pytest -Werror tests/test_supplier_*.py`（PowerShell使用
  Get-ChildItem展开参数）；Linux相同测试选择、锁定依赖、只读源码挂载。
- [x] 执行 `uv run --no-sync ruff check .`、`ruff format --check .`、`compileall`；
  缓存放 `.temp/`。构建wheel核对新模块及资产隔离。
- [x] 同步根目录交付记录、使用指南和工作树文档；重算source-sha256并同步候选模板，
  再运行五项生产门禁，缺外部材料继续blocked。

本任务不暂存、不提交、不推送、不采购、不调用真实账号、不发送邮件、不迁移、不部署。

集中复核确认并统一修复两项：报告服务不执行样本包字节序列化，16 MiB预算只作用于
显式保存；共享服务在质量检查和包构造完成后再次读取操作时间并核验授权。
两个回归反例均已实际观察RED（`.temp/n1-review-red.log`）。
最终Windows/Linux供应商全套各361 passed、0 skipped、0 warnings；
Ruff、335文件格式、编译及wheel核验通过。五项生产门禁保持blocked。
完整证据见 [工程交付记录](supplier-ingestion-implementation.md#n1不可变标准样本归档)。
