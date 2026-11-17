# DragonRadar 下一阶段开发计划

> **For agentic workers:** 后续逐切片实施使用 subagent-driven-development 或 executing-plans，
> 按下列验收项跟踪；本文件为下一阶段路线图，尚未实施的新接口须先冻结详细规格。

**Goal:** 依据真实许可与字段材料推进供应商接入和生产研究；标准样本独立核验入口已实现。

**Architecture:** 不可变标准样本归档与独立核验CLI已实现，复用现有读取和质量检查，
仅输出安全报告。
真实材料到位后接入现有生产输入、签章验证和研究 worker，不重复建设这些模块。
标准样本、生产输入和历史 PIT 证明保持各自契约与信任边界。

**Tech Stack:** Python、Pydantic、pytest、uv、Ruff；沿用现有 PostgreSQL、
本机 CLI、研究内核和 CI。

日期：2026-11-17。状态：N1、N1B、N1C完成；N2围绕这些切片的本机验收完成。
用户已授权主要功能整合到本地 `main`。真实数据与生产验收环境暂缓，
见 [后续待办](TODO.md)；下面的历史切片、材料要求与验证事实保持原记录。
正式CI待授权提交/推送。本次未查询邮箱。
既有验证事实见 [N1交付记录](supplier-ingestion-implementation.md#n1不可变标准样本归档)。

## 当前基线

- 已完成 HTTPS 采集、分块归档与恢复、只读核验、显式映射、样本质量验收及整链 CLI。
- N1历史验证为Windows、Linux 各361项供应商测试通过。
  N1B历史验证为两平台各407项；N1C最终验证为两平台各412项。
  全仓Ruff、338文件格式、编译及wheel实际保存/读回、授权到期回滚和独立CLI通过。
  最新证据见 [N1C交付记录](supplier-ingestion-implementation.md#n1c标准样本持久化授权边界收尾)。
- `archive_sample_cli --sample-output` 已能保存合格标准样本；
  `sample_archive.read_sample_archive` 已能按调用方预期摘要和当前完整授权核验读取。
  N1B已新增 `sample_archive_cli` 和 `scripts/verify_normalized_sample.py`，
  独立核验仅需标准包、外部保留摘要和当前完整授权。
- 生产签章验证、不可变生产输入、FAC-R01 物化、生产研究 worker 和影子记录模块已有实现。
  不将它们列为待从零开发事项。
- 已登记 iFinD 申请投递成功；发送记录未登记许可获批。
  本次未查询邮箱，因此不对是否收到新回复作判断。
- 最新生产诊断为五项 blocked，外部信任策略未配置；本次不修改其状态。

## 优先级与依赖

| 顺序 | 工作 | 启动条件 | 完成标志 |
| --- | --- | --- | --- |
| N1，已完成 | 不可变标准样本归档 | 现有离线链路 | 合格样本已可持久化及通过API重新核验 |
| N1B，已完成 | 标准样本独立核验CLI | 现有样本包读取API | 无需原始采集归档，用一条离线命令核验并输出安全报告 |
| N1C，已完成 | 标准样本持久化授权边界 | N1B已有on_durable回调 | 保存API及整链CLI拒绝最终fsync期间授权过期的成功产物 |
| N2，围绕 N1B 验证 | 跨平台回归及wheel核验 | N1B实现完成；保护既有修改 | 新入口在Windows/Linux及wheel中可用；正式CI另需授权提交/推送 |
| N3，材料到位后 | iFinD 真实配置及首批样本 | 实际许可、字段、日历、账号与额度 | 小批真实归档及缺项报告，可追溯到原始交付 |
| N4，N3及证据齐备后 | 真实输入接入现有生产计算 | 签章、PIT、快照、规则及完整依赖 | 获授权输入实际驱动内核，输入和结果摘要可复核 |
| N5，运行前提满足后 | 真实日终影子运行 | 可运行的真实依赖和批准日历 | 连续至少20个真实交易日的原始证据链 |

## N1：已完成的不可变标准样本归档

已完成共享服务、不可变包契约、CLI显式保存和按预期摘要读取。
下面保留已完成范围及验收项，避免把已有能力再次列为新开发。

已新增与调整的文件，均位于 `.worktrees/task27-research`：

| 文件 | 职责 |
| --- | --- |
| `src/dragon_radar/ingestion/archive_sample.py` | 提取可复用的归档映射及样本验收服务，返回标准样本和安全报告 |
| `src/dragon_radar/ingestion/sample_archive.py` | 独立样本归档契约、canonical序列化、私有独占写入、摘要核验读取 |
| `src/dragon_radar/ingestion/archive_sample_cli.py` | 调用共享服务；增加显式样本归档参数，保持现有默认报告行为 |
| `tests/test_supplier_sample_archive.py` | 归档成功、篡改、权限、预算、身份漂移及失败无产物的反例 |
| `tests/test_supplier_archive_sample_cli.py` | CLI归档与报告一致、既有参数兼容、授权到期和输出预留顺序 |
| `docs/supplier-ingestion.md` | 样本归档格式、命令及使用边界 |

本切片必须明确以下契约：

- 包包含完整标准 SampleRequest、配置字节摘要、采集指纹及每块原始归档摘要、
  实际首次接收时间；不包含令牌、原始响应或整份授权证明。
- 标准样本内的授权字段属于私有数据；对外报告仍只包含安全摘要。
- 仅当映射与质量验收均 pass 时生成样本包；blocked/fail不产生可消费样本。
- 保存后按摘要读取，拒绝覆盖、链接、重复键、超预算和非canonical内容。
  使用现有 `secure_output`、`read_asset`、`canonical_json`，不新增权限兜底。
- CLI默认仍只验收；新增持久化操作须显式指定，并在处理前预留目标。
  多产物失败时不得留下可被误认作完成的样本包，详细规格须定义完成标记或发布顺序。
- available_at、as_of、provenance及complete_pages原样保留。
  固定 `production_approved=false`、`historical_pit_status=unverified`。
- 这是本机研究的标准样本归档，不是 `dragon-radar-production-input/v1`；
  不调用生产签章入口把当前回补样本转换为历史PIT已验证输入。
- 读取和使用时重新核验当前适用授权；本机存储、数据导出和公开分享分别依其许可判断，
  不因新增文件格式而授予额外权利。

实施与验收顺序：

- [x] 冻结单切片规格，包括schema、大小上限、摘要范围、失败清理和读取授权规则。
- [x] 写关键失败测试并实际观察RED；实现共享服务和最小归档接口，观察GREEN。
- [x] 验证归档往返后精确Decimal、日历、记录顺序、实际接收时间及授权内容一致。
- [x] 验证缺历史、异常映射、过期授权、篡改和既有目标均无法产生合格样本包。
- [x] 一次集中独立只读复核，统一修复可证实问题，再运行适当的Windows/Linux回归。
- [x] 同步文档、源码身份及候选模板；不重写上一轮验证事实。

## N1B：已完成——标准样本独立核验CLI

本切片解决合格样本保存后仅有Python API可独立复核的问题；现有整链CLI要求原始采集归档、
request、mapping、extraction及空样本模板。新增独立入口在备份恢复或重复使用时，
只凭标准样本包、独立保存的预期摘要和当前授权完成复核。
该入口验证已保存的工程样本，不做数据导出、生产转换或研究计算。

推荐方案是增加薄CLI并复用现有读包服务。
仅补Python调用示例的开发量最小，但不能形成稳定命令入口；建设批量样本目录或
自动生产转换会扩展索引、许可和信任边界，本轮没有实际需求，不列入此切片。

已实施文件，均位于 `.worktrees/task27-research`：

| 文件 | 职责 |
| --- | --- |
| `src/dragon_radar/ingestion/sample_archive.py` | 最小提取可复用的核验结果，保留既有严格读包API与失败行为 |
| `src/dragon_radar/owner/files.py` | 可选on_durable回调，在最终fsync后及安全回滚作用域内复核授权 |
| `src/dragon_radar/ingestion/sample_archive_cli.py` | 参数、私有输入、安全报告、退出码及输出独占预留 |
| `scripts/verify_normalized_sample.py` | 薄脚本入口，避免与既有供应商行映射验收脚本混淆 |
| `tests/test_supplier_sample_archive_cli.py` | 成功、授权、篡改、权限、预算、输出冲突及实际入口反例 |
| `docs/supplier-ingestion.md` | 独立核验命令、报告和使用边界 |

已可使用的命令：

```powershell
uv run --no-sync python scripts/verify_normalized_sample.py --sample "<私有标准样本包>" --sha256 "<独立保存的64位SHA256>" --grant "<当前私有授权文件>" --output "<新私有报告文件>"
```

详细规格已在实施前冻结，见 [N1B规格](supplier-normalized-sample-cli-plan.md)：

- sample、sha256和grant必需；output可选，stdout始终是安全JSON。
  SHA256必须由调用方独立提供，不能从待核验包读取或重新计算后当作预期值。
- 默认实际当前UTC，命令不提供回填接收时间或绕过当前授权的选项。
  测试通过注入时钟或当前有效的离线fixture验证，实际入口仍检查当前授权。
- 复用现有16 MiB包预算、strict_json、canonical核验、私有文件及完整grant一致性规则；
  不放宽现有错误条件，不重新映射原始供应商响应。
- 将既有质量检查的安全报告作为核验结果复用，避免一次核验重复执行整套质量计算。
  保持现有 `read_sample_archive` 的公共签名和失败时不返回样本的契约。
- 若指定output，在读取grant和样本前独占预留，拒绝与输入冲突或覆盖既有文件。
  禁止输出完整标准样本、证券标识、价格、授权reference、凭据或输入路径。
- 报告保留安全摘要、覆盖数量、问题码、kind/test_only与retrospective状态；
  固定production_approved=false、historical_pit_status=unverified。
  成功表示当前工程核验通过，不改变原始接收事实。
- 退出码沿用0=pass、2=授权/证据/数据缺项blocked、1=结构/完整性/权限/冲突fail。
  具体错误映射须在规格中逐项固定，不能因包装CLI而吞掉原错误或回退成功。
- 失败诊断可正常写入；报告写入或fsync失败清理输出预留并返回fail，
  不保留成功报告作为错误的完成证据。

验收顺序：

- [x] 冻结参数、报告schema、错误映射、时间与失败清理规格。
- [x] 写关键反例并观察RED，再实现最小共享核验结果和薄CLI。
- [x] 验证无原始采集归档时，仅凭完整样本包、正确摘要和有效当前授权可pass。
- [x] 验证错误摘要、非canonical、重复键、权限/链接、超预算、授权到期/变更均关闭核验。
- [x] 验证输出预留早于输入读取，stdout/文件一致，异常信息不泄漏私有数据。
- [x] 验证核验全程不修改样本、不构造网络传输、不读取令牌或加载数据库。
- [x] 一次集中独立只读复核，统一修复后完成N2适当回归和构建核验。
- [x] 同步使用文档、源码身份及候选模板；五项生产门禁按真实证据保留。

## N1C：标准样本持久化授权边界收尾

用户要求继续开发后，确认N1的保存API及整链CLI未接入N1B新增的
fsync后授权回调。本切片修复既有存储授权契约的遗漏；
详细范围见 [N1C规格](supplier-sample-durability-plan.md)。

- [x] API及report-only/sample-only/两输出的5个反例实际RED。
- [x] 最小接入on_durable，当前授权失效时安全回滚，不返回可消费样本。
- [x] 保留先报告后样本顺序；样本最终失败时先行报告仍不是完成标记。
- [x] 相关8项GREEN；Ruff、338文件format、compileall、wheel实际API与CLI通过。
- [x] 集中独立复核与Windows/Linux最终回归关闭。
- [x] 同步最终交付记录及候选身份；五项生产门禁保持真实blocked。

## N2：围绕新切片的回归和构建验证

现有 `.github/workflows/ci.yml` 的全量pytest已覆盖自动发现的供应商测试，
因此先核对现有覆盖情况，避免额外叠加整套重复测量。
本机阶段完善精确命令、测试选择及安全离线fixtures；必要的CI配置修改单独列明。

- [x] N1：标准样本归档测试在Windows和Linux上均被发现并执行。
- [x] N1：构建包含新模块、模块入口可用，且不包含供应商原始资产、配置或秘密。
- [x] N1B：新增独立核验CLI通过Windows/Linux回归及wheel模块入口核验。
- [ ] 保持只读/私人构建物理隔离、现有Playwright覆盖和P95≤2.0s性能门禁。
- [ ] 正式CI须绑定实际提交和构建产物；未获授权提交/推送前，本机报告不能改称正式CI。

N1已有本机证据保留。N1B实施后补充新CLI的Windows/Linux实际执行与wheel入口检查；
不默认重跑无变动的数据库、前端及性能整套，不增加重复CI测量或降低既有门槛。
正式CI继续待实际授权提交/推送。该工作不启动无关前端改版或数据库重构。

## N3：真实供应商接入

N1B不构成N3的材料前置条件；实际材料先到位时，可优先按既有入口核对和采集。
生产推进的主要依赖仍是真实许可、响应结构及适用证据，不以新增CLI代替这些材料。

已有通用传输与显式映射，无需先安装SDK。实际响应不同于测试结构时，
依据厂商文档和原始小样配置字段、路径、单位及枚举；不猜列名和分页协议。

材料准备顺序：原始许可/试用条款与适用主体，实际字段及小样，
明确交易日历和证券映射，最后是已授权账号、额度及调用约束。
形成确认后的request/grant/mapping/extraction/sample配置，
先运行既有 `collection_cli` 预检，再在真实调用获授权后显式执行小批采集。
不以填写approved=true替代外部许可，也不把测试映射当作真实iFinD字段模板。

- [ ] 核对许可主体、期限、本机归档/备份/展示、导出及AI用途的适用范围。
- [ ] 冻结来源版本、明确证券代码关系、交易日历、响应结构和分页完整性依据。
- [ ] 核对历史上市/退市、ST/停复牌、OHLC、成交量额、复权和限价事实覆盖。
- [ ] 按实际额度做小批采集，保留真实首次接收时间，先执行现有整链离线验收。
- [ ] 补齐缺项或登记不支持范围；样本pass只表示工程合格，不自动通过real_input。

当前样本验收固定20日窗口，需要21个会话；
FAC-R01的60/120日方法分别需要61/121会话。后续方法选型必须按其完整契约验收，
不得用21会话样本证明全部方法已具备。

## N4：真实输入与生产研究

复用 `src/dragon_radar/research/production.py` 的既有生产契约和计算装配。
已有归档/物化/worker不重写；只新增经过规格确认的实际来源适配。

- [ ] 配置外部确认的发行方、公钥、适用范围及信任策略SHA256。
- [ ] 核验原始接收证明、批准方法、行情/题材快照和真实publication序号。
- [ ] 补齐所选方法需要的基准、题材及其他域依赖；保持行情与题材分离发布。
- [ ] 运行既有内核并保存实际输入/配置/结果摘要、开始结束时间及独立批准证据。
- [ ] 核验取消、预算、授权到期、PIT/版本漂移均关闭计算；API GET不重算。

当前获得的历史回补数据不能恢复过去的真实接收事实。
只有原始历史PIT证据齐备，或从当前开始形成合法实时接收链，
才能支持对应时点的生产研究。

## N5：逐日运行及其他生产材料

复用 `production/shadow.py` 与 `scripts/collect_shadow.py`，
按批准日历记录真实日终输入、版本、实际运行、异常和人工复核。
记录追加不可变，重复日期不得增加计数；语义版本变化依既有契约重新累计。
20个真实交易日不是可以通过批跑20个历史日期完成的开发工时。

以下材料可并行准备，但各自是独立工作流，不合并为N1的实现范围：

| 门禁 | 后续工作 |
| --- | --- |
| official_rules | 冻结实际规则版本和适用范围，取得官方归档、独立复核与批准 |
| real_model_evaluation | 至少100个唯一真实人工样本、development/frozen_test无泄漏、真实模型输出及独立证明 |
| public_sharing_permit | 明确发布主体、目标、受众与内容范围，取得相应再分发许可 |
| shadow_run_20_trading_days | 按真实日终累计原始接收、执行和复核证据 |

## 执行边界与阶段结束

N1B、N1C与N2本机验收已完成；后续N3/N4按实际材料启动，
材料齐备时优先推进真实接入。N1已关闭，不重复开发归档或生产输入内核。
具体实现继续在已有工作树进行，保护既有未提交修改。
本次规划未授权采购、真实账号调用、邮件发送、提交/推送、迁移、发布或部署。
本阶段交付标准样本归档时，生产五门禁仍按真实证据输出，缺项保持blocked。

依据：
[最新交付记录](supplier-ingestion-implementation.md)、
[供应商使用指南](supplier-ingestion-guide.md)、
[既有生产解锁设计](production-readiness-unblocking-design.md)、
[生产输入契约](production-inputs.md)。
