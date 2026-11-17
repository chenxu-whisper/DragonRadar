# 生产证明接入

`data/suppliers.json` 与本目录已建立。当前名册为空、证明索引为空，
尚无可认证的生产授权或实跑材料；创建入口不等于五项门禁通过。
`*.template.json` 是待填写模板，包含 null，不能直接作为生产配置。
验证器不会自动读取模板。

首个候选的准备范围已写入根工作区 `docs/production-candidate-scope.md`：
沪市主板普通 A 股、日线、FAC-R01 的20日相对强弱、仅本机 baseline 研究。
模板中的用途、范围和数据清单是拟定申请目标，不是已获许可声明。
未取得的实际版本、批准方法及日期仍保留 null。
授权主体按用户声明填写“陈旭”，属于个人申请目标，尚未获供应商授权；
不得直接复制为正式 candidate.json/context.json。

## 配置供应商

根工作区的 `data/suppliers.json` schema 为
`dragon-radar-production-suppliers/v1`，`suppliers` 为数组。
仅在取得真实授权后加入供应商，每项恰含：

| 字段 | 填写依据 |
| --- | --- |
| supplier_id | 真实供应商标识 |
| source_id / source_version | 实际来源及版本，与原始批次一致 |
| domains | market、state、calendar、topic、benchmark 中获授权的唯一成员 |
| grant | 下述 SourceGrant，与供应商许可及输入批次完全一致 |
| provenance | real；测试、演示和合成数据不得填写为真实数据 |
| methods | 因子标识到外部批准方法版本的映射，与 candidate.methods 一致 |

`grant` 显式填写 `source_id`、`approved`、`reference`、`starts_at`、
`expires_at`、`local_storage`、`local_display`、`export`、`ai`、`sharing`。
时间使用真实授权的 UTC 生效/到期时间；各项权利只能按授权原文填写。
内部存储/展示权利不推导出导出、AI 或公开分享权利。
空名册没有任何生产数据使用权限。

冻结的供应商材料必须与配置文件逐字节 SHA256 一致。
修改名册后须重新取得适用证明，不能仅改签名记录中的摘要。

## 候选与使用范围

依据已确认的真实候选填写 `candidate.template.json`，保存为
`candidate.json`；依据实际拟发布主体和内容填写 `context.template.json`，
保存为 `context.json`。消除所有 null，核对版本和授权范围。

六个版本恰为 code/dataset/rules/model/prompt/retrieval。
code 应通过正在运行的验证器工作树计算，不能使用主工作区旧提交：

```powershell
$codeRoot = 'E:\ByteDance\Projects\Code\DragonRadar'
Push-Location $codeRoot
try {
    & "$codeRoot\.venv\Scripts\python.exe" -c 'from dragon_radar.production.cli import CODE_ROOT,current_source_identity; print(current_source_identity(CODE_ROOT))'
} finally {
    Pop-Location
}
```

其他五项填实际冻结配置版本；不得用虚构版本或占位字符串。
`methods` 填已批准方法映射；`regulation_rules` 填排序后的
`rule_id:rule_version`；`rule_scope` 填真实适用交易所、板块、证券状态与交易日；
`official_source_hosts` 填经确认的官方主机集合。
context 的 subject/purpose/target/audience 必须与独立许可逐项一致，
content/data 填实际拟发布集合。

## 待取得材料

| 门禁 ID | 必需的真实材料 | 通过条件 |
| --- | --- | --- |
| real_input | 供应商授权、冻结 bundle、真实 receipt/PIT/快照、实际 run | 独立签章绑定配置字节；既有内核复算归档结果一致 |
| official_rules | 官方原文归档、规则定义、最新生命周期、方法与样例 | 独立批准绑定官方主机、摘要及候选范围；规则 active，样例重跑一致 |
| real_model_evaluation | 模型身份、原始输入输出、评估 manifest、100 个唯一人工样本及人工证明 | 两个非空无泄漏 split；各自事实支持率 ≥95%；数值/复算 100%；安全硬失败 0 |
| public_sharing_permit | 有权批准者签署的主体、用途、目标、受众、内容和数据许可 | 范围逐项匹配，明确允许再分发，限制均满足 |
| shadow_run_20_trading_days | 事先批准的官方日历、原始逐日记录、逐日独立签章、真实 bundle/run/supplier | ≥20 个连续完整交易日、相同语义版本、P95≤2秒、GET 零写、质量失败 0 |

影子历史必须来自实际当日接收、执行和复核，不能用回填的 20 行日志替代。
人工证明必须由真实标注者及获授权发行方确认。
本目录不生成生产密钥、批准身份、人工标签、供应商许可或历史记录。

取得材料后，把原始材料、公钥和 detached 签名放入同一受保护资产根。
向 `index.json` 的 evidence 加入已认证记录的相对路径：
real_input、official_rules、real_model_evaluation、
public_sharing_permit、shadow_run_20_trading_days。
当前索引为空；不要添加指向不存在文件的占位条目。

完整签名和材料 schema 见实现工作树的
`docs/production-evidence.md`、`docs/production-inputs.md`。
签名 JSON 必须 UTF-8、无 BOM、无尾换行，使用
`json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)`。
算法固定 RSA-PSS / SHA256 / MGF1-SHA256 / 32-byte salt。
记录同时绑定候选六版本、审批身份、有效期、范围和材料摘要。

## 受保护文件与外部信任

现有入口已按项目私有文件规范设置权限。后续导入/编辑配置、材料或签名后，
使用实现工作树的 `scripts/private-common.ps1` 中
`Set-PrivateFileAcl`，再运行 `Assert-PrivateFile`；
新增资产目录使用 `Initialize-PrivateDirectory`。
不要为既有工作区父目录批量改权限。
拒绝 symlink/junction、硬链接、越界路径与过宽文件权限。

本次环境为 PowerShell 7，旧助手中的实例 `SetAccessControl` 不可用；
Windows PowerShell 5.1 下同时设置 owner/DACL 也被当前令牌拒绝。
本次保留已经正确的当前用户 owner，通过 `Set-Acl` 仅设置 DACL，
关闭继承并只允许当前用户与 SYSTEM，随后通过 `Assert-PrivateFile`
及验证器 `read_asset` 检查。后续遇到相同环境可使用这一方式，
不能把权限设置失败当作检查通过。

外部信任策略须由获授权人员从独立渠道提供，并独立确认其 SHA256。
schema 为 `dragon-radar-production-trust/v1`，
包含 issuers/revoked_approval_ids/revoked_evidence_ids。
策略中的公钥摘要、门禁、scope、approver 和撤销状态必须适用。
工具不会信任证明目录自带的策略；本目录不提供自签信任策略。

## 验证命令

当前无外部策略时可运行，预期退出 2 并输出 blocked：

```powershell
$codeRoot = 'E:\ByteDance\Projects\Code\DragonRadar'
& "$codeRoot\.venv\Scripts\python.exe" "$codeRoot\scripts\verify_production.py" `
    --workspace 'E:\ByteDance\Projects\Code\DragonRadar' `
    --output 'E:\ByteDance\Projects\Code\DragonRadar\docs\production-readiness-status.json'
```

真实材料就绪后，在该命令中加入：

```text
--trust-policy <外部批准的受保护策略绝对路径>
--trust-policy-sha256 <独立确认的策略文件SHA256>
--openssl <OpenSSL可执行文件绝对路径>
```

退出 0 表示五项外部门禁通过；1 表示配置/证明无效；2 表示材料/工具缺失。
`input_availability=present` 仅表示入口存在。
即使五项通过，`production_release` 仍须独立发布门禁、可信自动化验收和发布授权。
