# 供应商接入工程交付记录

日期：2026-11-16。
代码工作树：`E:\ByteDance\Projects\Code\DragonRadar\.worktrees\task27-research`。
批准依据：`supplier-ingestion-design.md` 与 `supplier-ingestion-implementation-plan.md`。

## 已实现

- `supplier_transport.py`：官方 HTTPS 主机边界、环境令牌、有界 POST、
  超时/状态码、安全错误码及递归凭据检测。
- `supplier_collection.py`：显式证券/日历分批、有界重试与请求间隔、
  请求/接收/持久化的授权检查、实际接收时间、私有不可覆盖档案和断点恢复。
  首次请求前核验目录、全部已有分块及标记；冲突/篡改拒绝继续。
- `supplier_mapping.py`：严格不可变 profile、显式字段和枚举转换、
  精确 Decimal 倍率、来源保护、明确股票身份与规范 ISO 日期。
- `sample_acceptance.py`：复用现有质量检查，验收首版 SSE/main/common_stock
  范围、历史状态、OHLC/单位/涨跌停边界、21会话预热与基准覆盖。
- `sample_cli.py` 与 `scripts/verify_supplier_sample.py`：无数据库离线入口、
  映射三参数联合校验、输入摘要、有界 JSON、脱敏报告及独占私有输出。

所有采集档案固定 `provenance="retrospective"`；
所有样本报告固定 `production_approved=false`、
`historical_pit_status="unverified"`。
工程合格不创建生产许可，也不生成此前历史决策时点的接收证明。

## 集中复核

实现按 TDD 验证；采集初始43个失败、映射初始74个失败后通过。
最终集中规格复核及独立只读审查证实并修复：

1. 数字日期被隐式解释为 epoch：日期边界改为规范 ISO，6个反例先失败再通过。
2. OHLC 高低价越过供应商明确涨跌停价仍可合格：8个反例先失败再通过，
   非法观察不计入有效覆盖。
3. JSON 键中的转义字符绕过已知令牌检测：2个反例先失败再通过，
   对解码后的键和值递归核验。
4. 部分断点冲突发现过晚：权限、不连续额外分块、仅标记和后续身份冲突
   均在首次请求前拒绝，测试发送次数为0。

## 验证证据

新增测试最终完整运行为212 passed、0 skipped、0 warnings（`-W error`）。
四个库模块合计行覆盖率93%；CLI 通过独立进程测试验证，未混入库覆盖统计。
证据：工作树 `.temp/supplier-tests-final.log`、
`.temp/coverage/supplier-final.xml`。
离线核心回归最终为958 passed、159 deselected、0 skipped、0 warnings
（`-W error`），证据位于 `.temp/supplier-core-offline-final.log`。
本轮可执行套件共1170项全部通过。

已观测全仓 Ruff check 通过、324个文件格式检查通过、
`python -m compileall -q src scripts tests` 退出0。
CLI 离线演示确认退出0/2/1及植入凭据不泄漏；
证据位于 `.temp/supplier-cli-demo.log`。

核心回归首轮为958 passed、159 skipped，跳过原因是未配置
`DRAGON_TEST_DATABASE_URL`。最终离线运行通过 `.temp/supplier_offline_pytest.py`
明确 deselect 159项依赖该不可用 fixture 的测试；不修改测试及质量阈值。
首轮未运行数据库集成验证；用户要求继续后，补验结果将在下文单独记录。

当前源码身份：
`source-sha256:17d0fa4d1b88b0a33ae6b53e5319be0b7d392c2142c1c21a16f042a4ebec8320`。
已重新计算并确认与 `docs/proofs/candidate.template.json` 一致；
原 `production-readiness-implementation.md` 的测试和构建摘要保留为历史事实。

## 外部门禁及使用

实际生产验证器重新运行退出2，五项门禁均 blocked：
real_input、official_rules、real_model_evaluation、public_sharing_permit、
shadow_run_20_trading_days。信任策略仍为 not_configured，
`automated_acceptance="unverified"`、`production_release="blocked"`。
本轮安全报告：工作树 `.temp/supplier-production-status.json`。

iFinD 申请等待回复。取得真实授权、接口字段、历史覆盖、单位和额度确认后，
才能使用实际配置开展真实样本采集；当前分批 payload 契约未获厂商验证。
首次合格样本之后仍需独立生产证据及真实逐日观察。

使用方式见 [supplier-ingestion-guide.md](supplier-ingestion-guide.md)；
完整 API 和文件契约见
[工作树文档](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/task27-research/docs/supplier-ingestion.md)。
首轮未暂存、提交、推送、迁移数据库、发布或访问厂商服务。

## 续做：补齐 PostgreSQL 集成验证

用户要求继续后，发现既有 `dragon-radar-foundation-test-db` 容器正在运行，
上一轮未设置测试连接变量。已验证数据库实际名称为 `dragon_test`，
连接限定 `127.0.0.1:55432`；不创建新服务、不接触应用生产库。
测试使用既有 fixture 在随机临时 schema 中执行并清理。
运行前记录11个既有测试 schema，保留原有对象，不做额外清理。
运行中一次连接观察确认：测试账号在 `dragon_test` 有1个连接，
其他数据库连接数为0；观察未读取业务查询或行情内容。

本次仅选择先前 deselect 的159项数据库测试，原958项离线核心与212项新增
测试结果保留。运行日志：工作树 `.temp/supplier-database-final.log`。
最终结果：159 passed、958 deselected、0 skipped、0 warnings（`-W error`），
耗时431.56秒。测试选择复核确认159/1117项被选中，
清单位于 `.temp/supplier-database-selection.log`。

结束后重新连接并比对 schema 清单，仍为原有11个测试 schema，
本次临时 schema 全部清理完成。累计 Windows 验证为1329项通过：
212项新增 + 958项离线核心 + 159项数据库集成。
未提交、推送、发布或变更生产库；五项生产门禁仍为 blocked。

另在临时 Linux Docker 容器中补验传输与归档恢复分支：
61 passed、0 skipped、0 warnings（`-W error`），耗时2.69秒。
采用既有测试镜像和冻结 `uv.lock`，只读挂载 src/tests/scripts 及项目元数据；
测试依赖仅安装在临时容器内，容器结束后自动删除，未安装厂商 SDK。
日志：工作树 `.temp/supplier-linux-collection.log`。
Windows/Linux 验证均绑定此前记录的相同源码身份，候选模板再次核对一致。

## 续做：采集配置预检与执行 CLI

用户明确继续开发后，按 `supplier-collection-cli-plan.md` 补齐
`collection_cli.py` 和 `scripts/collect_supplier_sample.py`。
默认仅验证私有配置、实际映射文件SHA256及三项身份、当前 UTC 存储授权、
归档根和有界参数；不发送请求或写采集档案。
显式 `--execute` 委托现有 collect 核验全部断点、分块发送和恢复。
输出在执行前独占预留，输入碰撞、归档目录内输出、覆盖或无法预留
均在发送前拒绝。报告只包含固定码、摘要、数量和成功采集指纹。

TDD 初始23 failed（入口未实现），日志
`.temp/supplier-collection-cli-red.log`；实现后23 passed。
补充测试发现不存在的归档根与不存在的探测文件均被底层表示为
MissingEvidence，导致默认预检误报成功。独立只读集中审查证实同一缺陷，
现先确认根目录存在再验证私有权限；反例由失败转为通过。
Windows 测试夹具子目录使用既有 Initialize-PrivateDirectory 初始化，
没有放宽产品文件权限。最终入口35 passed、0 skipped、0 warnings，
日志 `.temp/supplier-collection-cli-green.log`。

全部供应商采集/映射/质量/CLI 回归为247 passed、0 skipped、0 warnings
（`-W error`），耗时95.39秒；日志
`.temp/supplier-with-collection-cli-final.log`。
本轮没有数据库功能改动，上一节159项数据库及958项离线核心证据
保留为之前源码身份上的验证事实，不标为本轮重跑。

Linux临时Docker容器传输/归档/新CLI 为96 passed、0 skipped、
0 warnings，耗时9.32秒；日志 `.temp/supplier-linux-collection-cli-final.log`。
启动既有Docker Desktop，使用已有测试镜像、冻结uv.lock和只读源码挂载；
34个依赖仅装入临时容器环境，退出后自动移除。
全仓Ruff check通过、327个文件format check通过；
compileall退出0，字节码统一置于 `.temp/compilecache-supplier-cli`。
使用指南及工作树文档已同步模块/脚本入口、参数、报告与恢复边界。

本轮源码及测试身份为：
`source-sha256:dd597b1f2a06f953e2a2a472908aae62d1cd159666c1811102f8b257291d4646`。
`docs/proofs/candidate.template.json` 已同步。
真实生产验证器再次退出2，五项门禁均blocked、信任策略not_configured；
证据 `.temp/supplier-collection-cli-production-status.json`。
本轮未暂存、提交、推送、发布、迁移数据库或访问真实厂商服务。

## 续做：完整归档读取与显式行映射

用户要求继续开发后，按 `supplier-archive-mapping-plan.md` 实现
`read_collection`、VerifiedCollection/VerifiedChunk 和
`supplier_archive_mapping.normalize_collection`。
只读入口复用现有请求身份、全部归档/标记摘要及接收/当前授权核验，
全部计划raw齐备才返回；不构造transport、读取令牌、补写标记或发送请求。
保留本次实际核验的同一份bytes，默认raw envelope及done标记总预算64 MiB。
采集器的原有preflight保持流式，不因新读取入口缓存全部原始响应。

映射桥接绑定实际mapping字节SHA256和profile/source身份，通过显式
字符串键/整数索引路径提取对象数组；两类行合计最多100000。
两类available_at均由所属分块实际first_received_at注入，日期常量拒绝；
通用MappingProfile仍保留原有常量语义。映射问题保留各类别全局行号，
不将成功行等同完整质量验收。结果固定retrospective、
historical_pit_status=unverified、production_approved=false。

TDD初始31 failed，因两个API尚不存在；日志
`.temp/supplier-archive-mapping-red.log`。实现及补充路径/问题保留/
接收时间/累计行预算反例后，新测试35 passed。
独立只读集中审查证实日期常量补造有效日期、done标记漏计总预算两项。
主代理增加3项反例，先观测全部失败，再修复：
桥接拒绝trade_date/limit_date/effective_from/effective_to/listing_date常量；
只读预算同时计入已读取的raw和done文件字节。
日志 `.temp/supplier-archive-mapping-review-red.log`。
最终新入口38 passed、0 skipped、0 warnings，耗时19.43秒；
日志 `.temp/supplier-archive-mapping-green-final.log`。

修复后最终供应商全套285 passed、0 skipped、0 warnings（`-W error`），
耗时87.66秒；日志 `.temp/supplier-archive-mapping-all-final.log`。
覆盖既有传输、采集/恢复、两类CLI、映射、样本质量及新归档桥接。
本轮没有数据库功能改动，未重复之前的核心/数据库整套验证。

最终Linux传输/归档/CLI/桥接134 passed、0 skipped、0 warnings，
耗时8.85秒；日志 `.temp/supplier-archive-mapping-linux-final.log`。
使用已有测试镜像、冻结uv.lock及只读源码挂载，依赖只安装到
自动删除的临时容器。全仓Ruff check通过、329个文件format check通过、
compileall退出0，字节码位于 `.temp/compilecache-supplier-archive`。
使用指南和工作树文档已同步API、预算、日期来源及样本质量边界。

最终源码/测试身份：
`source-sha256:60bffd881a68df82311e947ed904248bd141a8388fa5daf41384a99016ec4c50`。
已核对并同步 `docs/proofs/candidate.template.json`。
生产验证器退出2，五项门禁均blocked，trust_policy=not_configured；
日志 `.temp/supplier-archive-mapping-production-status.json`。
以前CLI/核心/数据库验证保留为其当时源码上的历史事实。
本轮未暂存、提交、推送、发布、迁移数据库或访问真实厂商服务。

## 续做：离线归档映射与样本验收 CLI

按 `supplier-archive-sample-cli-plan.md` 新增
`dragon_radar.ingestion.archive_sample_cli` 及薄脚本
`scripts/verify_supplier_archive.py`，串联完整归档核验、显式映射、
SampleRequest构造与样本质量检查。必需request/grant/mapping/extraction/sample
五项私有配置及archive-root；每项配置限4 MiB，归档总预算仍64 MiB。
不构造传输、读取令牌或加载数据库，也没有联网开关。

样本模板bars/security_states/limit_up_events必须为空，来源/版本、
完整grant和calendar须与采集请求绑定；trade_date为采集最后session，
provenance固定retrospective。映射失败保留全部安全问题并停止质量验收；
不回退或混入旧样本行。available_at保留每块实际接收时间，
明确as_of早于任一receipt则失败，不自动回写或调整。
股票池、复权/池版本、基准、决策会话及complete_pages仍为显式声明。
输出私有独占、在归档目录外，预留失败不处理归档。

TDD首轮37 failed（36项缺入口、一项fixture误用raw文件名），
日志 `.temp/supplier-archive-sample-red.log`。
实现后36 passed，修正fixture使用CollectedChunk.raw_path。
新增动态时间授权反例先RED（过期仍返回0），日志
`.temp/supplier-archive-sample-clock-red.log`；修复为默认动态UTC重新授权，
最终质量验收再次读取操作时间，`--now`仅作显式离线重放。
最终新套件38 passed、0 skipped、0 warnings，耗时36.98秒；
日志 `.temp/supplier-archive-sample-green-final.log`。

一次独立只读集中复核未发现可证实的问题，语法及非法时钟安全JSON复核通过；
复核者未声称独立重跑完整测试。
最终Windows供应商全部八文件323 passed、0 skipped、0 warnings
（`-W error`），耗时95.79秒；日志 `.temp/supplier-archive-sample-all-final.log`。
Linux同八文件323 passed、0 skipped、0 warnings，耗时43.32秒；
日志 `.temp/supplier-archive-sample-linux-final.log`。
使用既有测试镜像和只读源码挂载，冻结uv.lock；34个依赖仅安装在
自动删除的临时容器。全仓Ruff check通过、332个文件format check通过、
compileall退出0，字节码位于 `.temp/compilecache-supplier-archive-sample`。

源码/测试身份：
`source-sha256:8b5cc8afd36485e75c6ade2cca9f69c085e71c6c61ed031a9d7dd56b1632cc2d`。
已同步并核对 `docs/proofs/candidate.template.json`；
生产验证器退出2，五项门禁仍blocked、trust_policy=not_configured，
证据 `.temp/supplier-archive-sample-production-status.json`。
使用指南及工作树文档已同步新CLI、空模板约束及动态时钟边界。
以前验证结果保持其当时源码身份上的历史事实，未重复核心/数据库全套。
本轮未暂存、提交、推送、发布、迁移数据库或访问真实厂商服务。

## N1：不可变标准样本归档

按用户批准的 `next-development-plan.md` N1切片及
`supplier-sample-archive-plan.md` 详细规格实施。
新增 `ingestion/archive_sample.py` 共享服务与 `ingestion/sample_archive.py`
严格样本包契约、canonical序列化、私有独占保存及按预期摘要读取。
现有CLI增加显式 `--sample-output`；默认仍只诊断，原报告字段及0/2/1退出码兼容。

包schema为 `dragon-radar-normalized-sample-archive/v1`，最大16 MiB，
保存完整SampleRequest、五配置实际字节摘要、采集指纹与每块原始/归档摘要及真实接收时间。
完整样本含私有授权及行情；不包含令牌、原始响应、路径或完整授权证明。
报告仅增加完整canonical文件的 `sample_archive_sha256`。
读取要求调用方独立保存的预期摘要和当前完整授权；拒绝非canonical、重复键、
超预算、链接、公开权限、身份/接收时间漂移及不合格质量，返回前重新授权。
当前授权内容须与归档完全相同。as_of、available_at、精确Decimal、日历、
记录顺序和complete_pages保持原值，固定retrospective、PITunverified及productionfalse。

两输出均在读输入前私有独占预留。报告先完成写入/fsync/关闭，之后再发布样本，
写入前后重新授权；blocked/fail及任何写入/fsync失败均清理样本预留。
报告不作为完成标记，最终样本写入失败时报告可能已经保存；
强制终止留下的空/部分文件须通过摘要及canonical核验拒绝，重跑不会覆盖。
本机完整性检查不等于供应商签章、历史PIT证明或生产准入，
也不授予额外展示、导出、AI或公开分享权利。

TDD首个往返测试因缺少共享服务模块而实际RED；实现后归档及既有CLI测试通过。
新增写入期间授权到期反例实际RED（返回0），补上写入后的授权核验后转GREEN。
一次独立只读集中复核确认两项：
默认报告路径不应受16 MiB归档字节预算影响；共享服务质量检查期间到期需在返回前再次授权。
两个回归反例均实际失败，记录 `.temp/n1-review-red.log`；
统一修复为内存中构造严格类型包、仅显式持久化时序列化，以及服务返回前fresh UTC授权。
额外验证三分块实际接收时间、顺序和 `10.00000000000000000000000001` 精确往返。
最终切片两个测试文件76 passed、0 skipped、0 warnings（`-Werror`），耗时77.59秒；
记录 `.temp/n1-slice-green-final.log`。

最终源码与测试身份：
`source-sha256:2faf63aa0c330bc2d7825fa22fd14acb8674c0c87d17fc58c046aed9a1ce7692`。
已同步并核对根目录 `docs/proofs/candidate.template.json`。
生产验证器退出2，五项门禁全部blocked、trust_policy=not_configured；
记录 `.temp/n1-production-status.json`。本轮未查询邮箱或调用真实厂商服务，
不据此判断是否收到新授权回复。以前验证结果继续保持其当时源码上的历史事实。

N2本机验证围绕本切片完成：全仓Ruff check通过，335个文件format check通过，
compileall退出0，字节码位于 `.temp/compilecache-n1-sample-archive`。
最终wheel位于 `.temp/n1-build/dragon_radar-0.1.0-py3-none-any.whl`，
确认包含共享服务、包模块及CLI；未包含供应商资产或配置，
直接从wheel加载模块和运行CLI参数失败路径得到安全JSON。
检查脚本 `.temp/n1_verify_build.py`；未改变版本号或发布。
Linux最终供应商全部九文件361 passed、0 skipped、0 warnings，
耗时68.82秒，记录 `.temp/n1-supplier-linux-reviewed.log`。
Windows相同九文件361 passed、0 skipped、0 warnings，
耗时196.06秒，记录 `.temp/n1-supplier-windows-reviewed.log`。
沿用既有测试镜像、只读源码挂载和冻结uv.lock，34依赖仅安装在退出自动删除的临时容器。
现有CI全量pytest自动发现新测试，无需增加重复任务；没有提交/推送，因此上述是本机证据，
不是绑定实际提交的正式CI。本切片无数据库或前端变动，未重复其既有整套门禁。
使用指南、工作树文档和下一阶段计划均已同步。工作树及既有受保护修改保留，
本轮未暂存、提交、推送、合并、打tag、迁移、发布、部署或访问真实厂商账号。

## N1B：标准样本独立核验 CLI

日期：2026-11-17。按已批准的下一阶段计划及
[详细规格](supplier-normalized-sample-cli-plan.md)实施。
新增 `ingestion/sample_archive_cli.py` 与薄脚本
`scripts/verify_normalized_sample.py`。仅需私有标准包、独立保存的摘要和当前完整授权，
无需原始采集归档；使用实际当前UTC，不提供 `--now`。

`sample_archive.verify_sample_archive`复用严格读包与一次质量检查，
返回安全报告，仅pass时附带私有包；旧 `read_sample_archive`签名不变，
非pass仍抛错且不返回样本。沿用16 MiB包与4 MiB授权预算、私有权限、
链接/重复键/canonical/schema检查及完整授权相等规则，核验前后重新授权。
报告保留kind/test_only、coverage、issues及安全摘要，
固定retrospective、PITunverified、productionfalse。
授权拒绝或质量缺项退出2；结构、完整性、权限及输出错误退出1。

报告在读取输入前独占预留，拒绝覆盖或输入冲突；
失败诊断可保存，stdout/文件JSON一致。写入/fsync失败清理预留，
成功与失败诊断均在最终flush/fsync之后、关闭文件之前重新授权；
期间失效回滚报告并输出blocked。共享 `owner.files.secure_output` 增加可选
on_durable回调，默认调用行为不变；回调仍处于已有安全回滚作用域中。
输入不修改，不构造网络传输、读取令牌、加载数据库或转换生产输入。
真实模块和脚本测试使用当前有效的合成授权，删除原始档案与配置后仍通过。

首个成功用例因缺少独立入口实际RED；实现后转GREEN。
测试覆盖摘要不符、非canonical、重复键、样本/授权超预算、
完整授权漂移、开始及处理中到期、缺历史/基准、非法行情、结构/血缘错误、
公开权限、硬链接、符号链接/junction、预留顺序、写入/fsync清理、
安全参数错误、无效内部时钟和kind/test_only。
Windows使用目录junction验证重解析祖先拒绝，Linux使用文件符号链接；
测试不要求Windows额外的符号链接创建权限。

验证证据位于项目外 `E:\ByteDance\Projects\Code\.temp\DragonRadar-N1B`：

| 验证 | 结果 |
| --- | --- |
| 最终Windows供应商十文件回归 | 407 passed，184.07秒，`windows-reviewed.log` |
| 最终Linux相同回归 | 407 passed，64.04秒，`linux-reviewed.log` |
| 最终新增独立CLI测试 | 46项，包含在两平台407项回归中 |
| 全仓Ruff / format | 0问题；338文件已格式化 |
| compileall | 退出0；字节码在项目外compilecache |
| 最终wheel | 新模块齐备，无私有资产；从wheel独立加载CLI实际核验并写安全报告pass，`wheel-reviewed.log` |

上述测试均使用 `-Werror`，0 skipped、0 warnings。
复核前历史验证为两平台各399项通过、扩展切片各43项通过；
日志 `windows.log`、`linux.log`、`windows-focused.log`、`linux-focused.log`保留。
上表为统一修复复核问题后的最终结果，不将旧结果重称为最终证据。
已有schema contract和migration manifest属于正常wheel内容。
本轮无数据库、前端或性能实现变动，不重复其既有整套验收或放宽门槛。
现有CI全量pytest会自动发现新测试；本机结果不称为正式CI。

一次集中独立只读复核发现三项可复现问题：
fsync期间授权过期仍发布pass、失败诊断丢失已解析grant而跳过写后授权、
其他来源的当前grant被误归为完整授权漂移fail。
三个回归反例均先实际RED，记录 `review-red.log`；
统一修复为fsync后在安全回滚作用域内执行授权回调、保留已解析grant，
以及完整grant比较前检查当前授权对实际样本来源的适用性。
三项反例及相关写入/失败清理共6项GREEN，0 skipped、0 warnings。
最终完整跨平台回归在上述修复后重新执行，两平台各407项全部通过。
N1B及围绕其实现的N2本机验收关闭。
当前源码/测试身份：
`source-sha256:5ddad3c0cbc5d436640e6f6c7174ecb3b29be365a40515dcf170328b7e622203`，
已同步候选模板。五项生产门禁均blocked、trust_policy=not_configured，
验证器退出2，记录 `production-status.json`。
本轮未查询邮箱，不判断是否收到新许可；不启动真实N3~N5。
既有修改保留；未暂存、提交、推送、合并、打tag、修改版本、发布、部署、
执行生产迁移或访问真实供应商。

## N1C：标准样本持久化授权边界收尾

日期：2026-11-17。用户要求继续开发后，检查发现标准样本保存API和整链CLI
仍在最终flush/fsync之前执行授权检查。该遗漏与N1B已修复的持久化边界相同；
本切片落实既有授权存储契约，不新增采集、配置或生产接口。
范围见 [N1C收尾规格](supplier-sample-durability-plan.md)。

新增5个反例并实际观察RED：API未抛错、report-only/sample-only/
两输出样本fsync失效仍返回pass，以及两输出报告fsync失效后遗留pass报告。
记录项目外 `E:\ByteDance\Projects\Code\.temp\DragonRadar-N1C\red.log`。
最小调整 `sample_archive.write_sample_archive` 和
`archive_sample_cli._publish`，复用已有 `secure_output(on_durable=...)`，
在最终flush/fsync后、仍处于安全回滚作用域内检查当前grant与实际source。
原有写入前后检查保留；回调不重复质量计算。
五项反例和三项旧写入/失败清理测试8 passed、0 skipped、0 warnings。

API持久化期间授权到期时抛storage_denied，不返回摘要且清理样本；
CLI返回安全blocked JSON及退出2。合格结果的报告与样本均使用该边界。
两输出仍报告先完成再发布样本：报告fsync期间失效清理两者，
样本fsync期间失效清理样本，先前完成的报告可保留且不能作为消费完成标记。
质量blocked/fail仍可保存诊断，不产生可消费样本。
采集器已经在fsync后发布前复核授权，无需修改采集协议。

全仓Ruff与338文件格式检查通过，compileall退出0。
最终wheel包含原有schema contract及migration manifest，无私有资产；
直接从wheel加载保存API，实际完成样本保存/读回、fsync过期清理及独立CLI
安全报告写入，均pass。记录 `wheel.log`；构建位于项目外 `build/`。
Linux供应商十文件412 passed，耗时58.19秒，记录 `linux.log`。
Windows相同十文件412 passed，耗时195.72秒，记录 `windows.log`。
两平台均使用-Werror，0 skipped、0 warnings。
一次集中独立只读复核未发现可证实的重要正确性缺陷；
独立运行两个切片测试文件81 passed，确认回调时序、回滚作用域、
当前source/grant与clock绑定、先报告后样本顺序及旧失败行为。
N1C及围绕该修复的N2本机验收关闭；没有重复未改动的前端、数据库或性能整套验证。

当前源码/测试身份：
`source-sha256:c88f1e32576999090e52639412c9b4f08c488b4a8208c6c7369b9015632df4ea`，
已同步候选模板。生产验证器退出2，五项门禁blocked、
trust_policy=not_configured，记录 `production-status.json`。
未查询邮箱、发送邮件、调用真实供应商或启动真实N3~N5。
本切片不修改schema、接收时间、质量/性能门槛或版本；
保护既有修改，未暂存、提交、推送、合并、发布、部署或生产迁移。
