# N1B：标准样本独立核验 CLI 规格与执行计划

日期：2026-11-17。用户已批准实施；已在现有 task27-research 工作树完成。
关闭证据见 [交付记录](supplier-ingestion-implementation.md#n1b标准样本独立核验-cli)。

目标：不依赖原始采集归档，仅凭私有标准包、独立保存的摘要和当前完整授权，
输出工程核验报告。无网络、令牌、数据库、生产转换或接收时间回填。

命令参数：`--sample`、`--sha256`、`--grant` 必需，`--output` 可选。
摘要严格为64位小写十六进制；禁止参数缩写，不提供 `--now`。
运行使用实际UTC；单测替换模块时钟，真实子进程使用当前有效合成授权。

复用16 MiB样本读取预算及4 MiB授权读取预算、私有文件检查、strict_json、
canonical、模型和完整授权相等检查。共享核验返回安全质量报告，
仅pass时带私有包；旧 `read_sample_archive` 签名不变，
非pass仍抛 `sample_not_qualified`，从不返回未合格包。质量检查每次仅执行一次。
共享核验前后重新检查当前授权；模型核验后，完整grant比较前，
先检查当前grant对实际样本来源的适用性。

报告沿用 `dragon-radar-supplier-sample-report/v1`，包含status、kind/test_only、
coverage、issues、input_sha256、样本摘要及lineage安全摘要。
固定provenance=retrospective、production_approved=false、
historical_pit_status=unverified。结构尚未核验时不声明kind或coverage。
禁止输出样本正文、证券标识、价格、授权reference、凭据、输入路径或异常文本。

| 退出码 | 条件与固定错误码 |
| --- | --- |
| 0 | 当前授权及质量全部pass |
| 2 | `storage_denied`；质量报告中数据/证据缺项blocked，保留原问题码 |
| 1 | `arguments`：缺参数、非法摘要、未知参数 |
| 1 | `input_unavailable`：授权文件不可读、权限、链接或预算失败 |
| 1 | `invalid_grant`：授权JSON重复键、无效模型或格式 |
| 1 | `sample_archive_integrity`：摘要或canonical不一致 |
| 1 | `sample_grant_mismatch`：完整授权内容漂移 |
| 1 | `invalid_sample_archive`：样本权限/链接/预算、JSON/schema/身份绑定失败 |
| 1 | `operation_time`：内部时钟无效 |
| 1 | `output_conflict`：报告与任一输入路径相同 |
| 1 | `output_unavailable`：报告预留、写入、flush或fsync失败 |
| 1 | 质量报告中结构/事实错误fail，保留原问题码 |

指定output时先核对路径冲突，然后在读取任何输入前独占预留。
成功/失败诊断均可写入，stdout与文件JSON一致。写入后重检当前授权；
期间失效时回滚文件并输出blocked。写入/fsync失败清理预留，
stdout仅输出fail，既有目标永不覆盖。
最终flush/fsync之后、文件关闭及回滚作用域退出前重新授权，
成功和失败诊断均适用。为此给共享 `owner.files.secure_output` 增加可选
`on_durable` 回调；默认行为不变，回调失败沿用现有安全清理。

实施顺序：

1. 关键成功、缺项、授权、篡改、预留与失败清理测试先运行RED。
2. 最小提取共享结果，新增CLI及薄脚本；运行GREEN和旧读包回归。
3. 补齐真实模块/脚本、跨平台权限、预算、时钟和安全输出反例。
4. 集中一次独立只读复核，统一修复证实问题。复核发现的fsync期间到期、
   失败诊断未保留grant、其他来源grant映射三项均先观察反例RED，再统一修复。
5. Windows/Linux供应商回归、Ruff/format、compileall、wheel实际入口。
6. 同步指南、交付记录、路线图及源码身份；五项生产门禁保留真实blocked。

不暂存、提交、推送、合并、tag、修改版本、发布、部署或执行生产迁移。
开发日志与缓存使用项目外临时目录；不清理既有开发产物。
