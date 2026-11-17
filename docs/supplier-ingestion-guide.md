# 供应商接入使用指南

开发工作树：`E:\ByteDance\Projects\Code\DragonRadar\.worktrees\task27-research`。
沿用现有环境，所有命令从该工作树运行。

完整配置格式、采集 API、归档恢复和质量检查说明见
[工作树使用文档](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/task27-research/docs/supplier-ingestion.md)。
设计及批准范围见 [supplier-ingestion-design.md](supplier-ingestion-design.md)。

采集配置预检（默认不联网、不写采集档案）：

```powershell
uv run --no-sync python -m dragon_radar.ingestion.collection_cli --request "<私有请求文件>" --grant "<私有授权文件>" --mapping "<私有映射文件>" --archive-root "<已存在的专用私有归档目录>" --output "<归档目录外的新报告文件>"
```

取得实际授权及字段确认后，在同一命令追加 `--execute` 才采集。
也可使用 `scripts/collect_supplier_sample.py` 加相同参数。
预检会绑定请求与实际映射文件的身份/SHA256，检查当前 UTC 存储授权、
私有输入和归档根、参数范围；默认不核验已有断点。
显式执行由已有采集器核验全部断点后恢复；已完成分块不重复发送。
令牌只从 `--token-env` 指定的环境变量读取，默认 IFIND_ACCESS_TOKEN；
不支持 `--token` 或回填接收时间的 `--now`。
报告输出须是归档目录外的新文件，预留失败时不会联网。
完整参数边界及报告契约见上述工作树文档。

已采集档案可通过 `read_collection` 离线完整核验，再用
`supplier_archive_mapping.normalize_collection` 按显式行路径映射。
映射配置须与请求绑定相同身份及实际文件SHA256；映射后的 available_at
取每块实际接收时间。完整raw但缺done标记可读，缺块/篡改/超预算会拒绝。
该路径不联网或修改归档，结果仍需执行样本质量检查；
API示例见工作树文档“从已核验归档映射”。

完整归档映射并验收可使用一个离线命令：

```powershell
uv run --no-sync python scripts/verify_supplier_archive.py --request "<私有request.json>" --grant "<私有grant.json>" --mapping "<私有mapping.json>" --extraction "<私有extraction.json>" --sample "<私有sample.json>" --archive-root "<专用私有归档目录>" --output "<归档外的新报告文件>"
```

模块入口为 `dragon_radar.ingestion.archive_sample_cli`。
extraction显式声明两类对象数组路径；sample使用完整SampleRequest，
bars/security_states/limit_up_events为空，provenance为retrospective。
来源/版本、完整grant和calendar须与采集配置一致；as_of须涵盖实际接收时间。
股票池、复权/池版本、基准、决策会话及complete_pages仍须明确声明。
任意映射问题使验收失败，缺事实使验收blocked；不会混入旧样本记录。
五项配置各限4 MiB且私有，输出独占并在归档外；不联网、不读取令牌。
默认在处理过程中重新检查当前UTC授权；`--now`仅用于离线重放。
报告固定retrospective、PITunverified和productionfalse。
详细格式与边界见工作树文档“完整归档直接映射并验收”。

保存可复用标准样本时，在完整归档验收命令追加
`--sample-output "<归档外的新私有样本文件>"`。只有pass才保存，
报告增加整份canonical包的 `sample_archive_sha256`。包含完整标准样本及采集血缘，
最多16 MiB；授权及行情属于私有数据。两个输出先预留，报告完成后再发布样本，
blocked/fail或写入失败不会留下可消费包。
N1C已将持久化完成授权检查接入标准样本保存API及整链CLI：
合格报告和样本各自在最终flush/fsync后、关闭文件前再次检查当前授权。
期间过期清理相应文件；API抛storage_denied且不返回摘要，CLI输出blocked并退出2。
两输出仍报告先完成再发布样本；样本最终失败时先行报告可能保留，
必须以实际样本包的读取核验为消费依据。

后续使用 `sample_archive.read_sample_archive(path, expected_sha256=retained_digest,
grant=current_grant)`，传入独立保存的摘要和当前完整授权；读取时重新校验质量、
完整性、私有权限及当前授权，授权内容必须与归档一致。
报告不是完成标记，消费以实际样本读取核验为准。接收时间、精确Decimal、日历与
complete_pages不变，仍为retrospective、PITunverified、productionfalse。
这不授予额外导出/分享/AI权利，也不替代生产签章。完整格式、失败清理及API见
工作树文档“不可变标准样本包”和 [N1规格](supplier-sample-archive-plan.md)。

已保存标准包可独立核验，不再依赖原始采集归档：

```powershell
uv run --no-sync python scripts/verify_normalized_sample.py --sample "<私有标准样本包>" --sha256 "<独立保存的64位小写SHA256>" --grant "<当前私有授权文件>" --output "<新私有报告文件>"
```

模块入口为 `dragon_radar.ingestion.sample_archive_cli`。
sample、sha256和grant必需，output可选；stdout始终输出安全JSON。
使用实际当前UTC，不提供`--now`；完整当前授权须与包内相同。
沿用16 MiB样本与4 MiB授权预算，严格校验私有权限、链接、重复键、canonical、
外部摘要和质量；只评估一次，不输出样本、证券、价格、授权reference或路径。
报告固定retrospective、PITunverified、productionfalse，保留kind/test_only及覆盖问题。
退出0=pass、2=授权拒绝/质量缺项blocked、1=结构/完整性/权限/输出错误fail。
报告先独占预留再读取输入，拒绝覆盖和输入冲突；
写入/fsync失败清理输出；成功/失败诊断均在最终fsync后重新授权，
期间授权失效在文件关闭前回滚报告并返回blocked。
详细错误映射见 [N1B规格](supplier-normalized-sample-cli-plan.md)。

canonical 样本验收：

```powershell
uv run --no-sync python -m dragon_radar.ingestion.sample_cli ".temp/sample.json" --output ".temp/sample-report.json"
```

供应商行映射后验收：

```powershell
uv run --no-sync python scripts/verify_supplier_sample.py ".temp/sample.json" --mapping ".temp/mapping.json" --bar-rows ".temp/bar-rows.json" --state-rows ".temp/state-rows.json" --output ".temp/sample-report.json"
```

输入文件需按真实交付配置准备；样例测试只用于离线工程验证。
退出码0为工程合格，2为缺项，1为结构/映射错误。
报告均保持 `production_approved=false`，历史 PIT 接收证明仍待核验。
采集保留程序化 `collect` API 并提供上述 CLI；正式调用前需已确认的接口参数、
真实 SourceGrant、环境令牌及专用私有归档目录。

已登记iFinD申请投递成功；本轮未查询邮箱，不判断是否收到新回复。
厂商授权与真实字段确认后，才能进行首次真实样本采集；
样本通过后继续独立生产证据链、正式 CI 和真实逐日观察验收。

数据库回归使用既有 `dragon-radar-foundation-test-db` 容器的
`127.0.0.1:55432/dragon_test`。连接配置见工作树 `docs/reset-running.md`；
仅设置测试连接变量不会改变生产连接。运行结果及临时 schema 清理证据见
[工程交付记录](supplier-ingestion-implementation.md)。
