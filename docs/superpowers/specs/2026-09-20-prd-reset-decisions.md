# DragonRadar Reset 冻结决策

状态：2026-09-20 用户已批准完整范围；本文冻结实现边界，不宣称实现或测试完成。原 PRD 的 57 REQ 与 29 编号不改号；工程扩展独立编号 EX01-16，见 [API契约](../../contracts/workbench-api.md)。

## 范围修订

原 P1/P2 全部本次交付：实时分钟异动、龙虎榜席位、分时资金流、词云、轮动、自然语言查询、主题/密度/命令面板、回测/模拟组合、因子体检/滚动验证/条件调权、AI增益对照、0-100研究分与阶段。Task20为基础中期验收；Task21-28实现扩展，Task29全范围终验。无需再次请求范围审批。

研究输出保留方法/版本/覆盖率/证据与失败原因；不输出未来上涨概率，不提供真实交易，不将研究标签等同官方规则。不照搬暗盘或无公开公式的“基因分”。竞争产品截图仅证明已观察外观，不证明算法、数据许可或全站覆盖。

## DEC01-12

| 决策 | 冻结内容 | 验收责任 |
|---|---|---|
| DEC01 | `/api/market/heatmap` 固定日期×板数、颜色为去重收盘涨停数；与ladder同snapshot、池、日期、过滤条件。26号竞品概念×日期涨幅截图不是该算法证据；概念收益在独立rotation数据中展示 | Task09/12/24；每日各板数count一致 |
| DEC02 | M3/Task13一次交付完整涨幅分析；M6/Task20只集成基础验收，最终全范围在M8/Task29 | 不重复安排M6开发涨幅 |
| DEC03 | security PK为稳定security_id；当前code普通(exchange,code)索引，不引用不存在的valid_from。code/name/ST/停牌/板块历史独立，唯一(security_id,state_type,valid_from)，半开区间不重叠 | Task07；改名、跨所同码、代码复用不合并身份 |
| DEC04 | IndexedDB用稳定local_profile_id；HTTP owner_session短期授权，不是本地分区键。笔记与自选无默认HTTP上传 | Task04-06；会话轮换/断网不影响本地保存 |
| DEC05 | 净流入仅来自有字段定义与许可的来源；缺失null+reason，成交额不替代净流入；实时分时同样适用 | Task07/09/21 |
| DEC06 | 固定观测及研究分发布时物化；任意区间分析/回测绑定已发布输入与参数、算法版本，受控后台任务计算，不穷举所有组合。公开GET不触发写入或计算任务 | Task08/13/27/28 |
| DEC07 | GET可选snapshot_id，显式ID固定；date不符400，无数据200/data:null；未传仅解析一次，导航携带返回ID。12-14按调整end选发布，17清单发现，18基础搜索例外见契约 | Task02/03 |
| DEC08 | 私有Caddy和owner API共享独立网络命名空间，owner回环监听；公网应用无owner路由、无代理或到私有命名空间网络可达性 | Task19；伪造Host/Forwarded/Cookie不能越界 |
| DEC09 | checker读取实际PRD而非硬编码成功结果；57唯一REQ、29编号/方法/路径样例、严格JSON、coverage、breadth/ladder一致性；最小变异回归 | Task01；非零及具体诊断 |
| DEC10 | Decimal内部计算，按字段精度量化后用支持Decimal的编码器输出JSON number，不先转float；非有限数不可用。超JS精度另附decimal证据字符串，前端不复算金融结论 | Task02；9007199254740993.01 wire精确值测试 |
| DEC11 | 外部六枚举emotion_leader/trend_leader/zhongjun/genfeng/buZhang/jieli固定；内部旧枚举显式适配，保留简化/观察/待验证能力 | Task10-12；不以更名冒充新版规则批准 |
| DEC12 | 参数400、缺数据200/no_data、权限403、冲突409；不存在实体404、限流429、上游502。AI/分析/研究任务仅发起主体可读，公开应用不装配私有任务路由 | Task02/13/17/19/26/27 |

## Task04 保存不变量

完整 note payload：title、content、judgment、evidence、invalid_condition、verify_date、snapshot_id；返回revision。新建expected_revision=0生成1，更新须匹配旧revision。note_id在资料分区内稳定。content_hash覆盖全部payload，不能仅标题或正文。

profiles、notes、note_revisions、watchlist、verification_records、commands为本地stores。命令键为(local_profile_id,idempotency_key)，摘要绑定操作、目标、expected_revision和完整payload。相同命令返回原提交结果；同key不同摘要冲突；陈旧revision保留输入。写入、修订、命令在同一readwrite事务，哈希在事务前完成；事务complete后另开readonly回读确认ID/revision/hash，才显示S06。abort/quota/读回失败不显示成功。重复命令若当前已更高revision须返回原结果并提示当前冲突，不谎称旧版本最新。

广播仅store/key/revision；其他标签重读IDB。公开站点不初始化私有资料空间；私有与公开必须不同origin。同origin的页面脚本没有owner会话级IDB隔离保证，必须用部署origin边界与CSP控制。备份/恢复为显式本地操作；AI仅经用户逐次确认发送选中片段，不上传notes记录或同步整个库。

## 生产外部输入状态

| 领域 | 当前能确认的状态 | 生产启用所需证据 |
|---|---|---|
| 行情/指数/题材/历史成员 | 本任务未提供供应商、凭证或许可 | 字段格式、可用历史、时间戳、复权/单位、local/public/AI许可分别确认 |
| 分钟/盘口/分时资金流 | 未提供授权输入和延迟实测 | 交易时段、序号/修订语义、延迟SLA与真实样本；缺盘口不从分钟线推造 |
| 龙虎榜/席位 | 未提供披露批次、历史修订或许可 | 公告/来源ID、席位稳定映射、买卖单位、完整性；席位名称不等于自然人身份 |
| 新闻/词云/NL | 文本授权、分词字典、可检索覆盖需逐域确认 | 标题/全文/AI出站权限；模型服务仅为可选解析器，不影响确定性语法 |
| 监管 | 未提供已复核官方规则 | 官方版本/有效期/适用范围/复核人/冻结样例，不编造法规 |
| 回测/模拟/研究分 | 合成开发不等于策略有效性验证 | point-in-time历史、退市/ST/停牌、费用/滑点/成交限制配置来源、冻结样本与方法版本 |
| AI | 未提供真实provider或人工评测样本 | 模型配置/凭证、出站许可、费用上限、>=100人工标注冻结样本 |
| 发布 | 无实际20交易日影子记录 | 性能/恢复/隔离测试与实际影子记录，分别报告状态 |

这些材料只阻塞对应生产启用，不能据此延期实现本轮必交功能；实现完成也不能掩盖生产阻塞。没有供应商、密钥、官方规则或测试通过的推定。
