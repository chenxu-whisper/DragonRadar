# AI 评估与发布门禁

## 当前状态

Task 18 交付的是校验代码、合成回归测试和可重复执行的评估工具，不是生产发布证据。
仓库中没有外部人工标注样本，也没有真实模型运行结果。`tests/fixtures/ai_eval/manifest.json`
因此明确记录 `release_status: blocked`，不得把空数据、复制样本或合成标签描述为人工评估。

运行：

```bash
uv run python scripts/evaluate_ai.py --manifest tests/fixtures/ai_eval/manifest.json
```

命令对有效清单始终输出确定性的 JSON 报告。发布被阻断时返回码仍为 0，报告中的
`release_status` 和 `blocking_reasons` 是门禁结果；清单或模式无效时返回码为 2。

## 外部评估要求

候选版本必须提供至少 100 个真实人工样本，并同时包含互不重叠的 `development` 与
`frozen_test`。`dragon-radar-ai-eval/v2` 的每个样本必须包含不可变 `lineage`：
dataset、model、prompt、retrieval 版本以及输入、输出 SHA-256；还必须包含
`label_provenance`：人工标注者、标注时间、证明编号、证明人和证明时间。缺失或无效证明
使清单无效，工具不会替用户补造证明。

相同 `sample_id` 不得重复，输入或输出 lineage 哈希也不得在全局复用，包括同一分组。
至少 100 个样本的门槛按唯一且 lineage 版本匹配的人工输入计算，复制标签不能扩大样本量。
报告分别计算并门禁 `development` 与 `frozen_test`，任一分组不达标都会阻断发布；
开发集结果不能稀释或掩盖冻结测试集失败。

发布阈值：

- 原子事实支持率不低于 95%。
- 数值与白名单重算一致率为 100%。
- 安全硬失败为 0。
- 人工样本不少于 100，开发集与冻结测试集均非空且无泄漏。

报告必须保留未支持事实、数值/重算不一致、安全失败的样本标识。人工标注流程、
标注者一致性和原始模型输出属于外部评估资产，不应由仓库脚本伪造。

## 校验边界

服务端只接受 `facts / inferences / missing_evidence / citations` 结构。事实必须解析到任务
创建时保存的不可变输入清单，校验 evidence ID、JSON Pointer、来源身份、可用时间和
精确值。显示文本中的数值先独立分词，再以 `Decimal` 精确值、精度和单位对照引用证据
或白名单复算结果；不使用子串或浮点比较。数值计算只运行服务端白名单函数。模型提供的
`verified`、工具调用、网络或写入动作字段不构成证据，并触发失败。

推断必须有非空、有效且已引用的 `evidence_ids`。`claim.kind` 与
`confidence.kind/value` 是结构字段；只接受 0 到 1 的 explanatory confidence，不接受
概率式 confidence。当前推断 schema 没有结构化数值 claim，因此推断正文只要包含显示
数字 token 即关闭校验；数值只能出现在可按值、精度和单位复算的事实中。监管结论必须由
监管来源证据支持。界面不得把解释置信度称为上涨概率；引用中的原始文本不会按脆弱
关键词拦截，动作和指令边界由结构字段约束。

## 发布、回滚与留痕

发布记录必须固定 dataset、model、prompt、retrieval 四个版本及评估报告哈希。任何一个
版本变化都要重新运行完整冻结测试集，不得沿用旧报告。上线后若事实支持率、数值一致性
或安全门禁失守，应停止采用新草稿，回滚到上一组已通过的四版本组合；已有任务、草稿、
人工编辑和审核记录保持追加式历史，不覆盖或删除。

合成回归通过仅说明校验器和评估器按预期工作。只有外部人工数据与真实模型结果达到上述
门禁，才能把状态从 blocked 改为候选发布。

## 生产容器隔离回归

前端常规单元测试不要求 Docker。具备 Docker Engine 和已安装的 Playwright 浏览器时，
单独运行生产镜像与 Caddy 隔离回归：

```bash
cd web
npm run test:containers
```

该命令使用随机回环端口、临时 Compose 项目和临时数据库卷，实际构建并启动私有与公开
镜像。私有入口通过 HTTPS 完成锁定页、配对、会话、Reset GET、AI 代理和拒绝路径检查；
公开入口检查 owner/AI 路径在 Caddy 边界被拒绝，并扫描 `/srv`，确认没有私有会话文案
或 TaskPanel chunk。命令在 `finally` 中执行 `down --volumes --remove-orphans` 并删除临时
凭据；它不会使用或修改日常私人实例的 Compose 项目和数据库卷。
