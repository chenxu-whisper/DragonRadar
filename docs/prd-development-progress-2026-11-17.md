# DragonRadar PRD 与开发进度对照：B1-B5 完成，八文件后端门禁通过

2026-11-19 授权的 C0 五项／C1 八项验收已关闭，见
[本批验收报告](acceptance/contract-c-2026-11-18.md)。独立规格／质量复审
及最终整体一致性复核均为 PASS。控制器最终 contract-runtime
`45252-1795068624675-c8683e1c` 与原有 production-isolation
`26524-1795069437461-7b30f253` 均 exit 0，实际清理 removed、
容器／卷／网络残留为空、秘密文件已删除。
专用 Linux Chromium 验证信任前拒绝及信任后严格 owner TLS，
两份真实 CLI 合成发布、证券／题材／文章／引用目标、独立复核与
P1 原始归档不变／P2 生效、来源及 owner 撤权清空。公开 54／owner
143 个 JS/CSS 状态、MIME、哈希通过；预期拒绝保留并精确分类。
原有 Edge 忽略证书的结果仅证明既有隔离范围。
最终 Node 146/146、TypeScript 通过；稳定完整前端为 47 文件／
1412 Vitest／96 Node；无数据库读取契约为 171 passed／24 deselected，
这 24 项集成用例未执行。Ruff／compileall／PRD 57／29／29／30 通过。
源码及日志身份、失败到通过历史保留在报告中。
2026-11-20 C2 六项有限验收已关闭：SPEC／QUALITY、最终 Linux185／320 单元、三全新构建、独立 Chrome66、清理与恢复后日常身份核对通过，见
[C2 执行记录](acceptance/contract-c2-2026-11-19.md)。
C3–C5 与 D 未执行，完整 `automated_acceptance` 保持 `unverified`；
57 REQ／16 EX 的主状态不因本次有限验收整体提升。

当前更新：2026-11-18，ActiveSDD B1-B5 在隔离
`E:/ByteDance/Projects/Code/.temp/DragonRadar-ContractB` 完成授权的有限契约范围。
需求基线：[开发需求 PRD](DragonRadar_开发需求PRD.md)。
逐项当前决定、实际命令／日志／源码身份及 C/D 剩余条件见
[B1-B5 当前状态](contract-status-2026-11-18.md)；完整 PRD、C 工程验收、
D 生产条件均未关闭，`automated_acceptance` 仍为 `unverified`。
本实现代理的隔离 worktree 阶段不提交／推送，不修改 main、运行实例、版本或部署。
控制器原计划授权的 reviewed/hash-safe 回传 main 为单独记录的动作，
回传清单为八文件，包含 `tests/test_reset_analysis.py`；本代理不执行或声称完成该动作。
2026-11-17 的本地 main 整合及下方旧验证数量仅保留为历史，不代表本批回传或当前全量。
真实数据接入和生产验收环境仍暂缓，见 [TODO](TODO.md)。

## B5 当前契约与证据

57 REQ／16 EX 共 73 行当前追溯保留。REQ 状态为 37 项有限实现、
3 项交互缺口、14 项待验证、3 项缺真实生产输入；EX 为 8 项有限实现、
8 项待验证。数量是状态分布，不是验收通过比例。
B2 显式未知 ID 的 None／真实发布路径 RED→GREEN 已保留；
B5 全范围回归统一为 404、精确安全 `NOT_FOUND`／`Resource not found`，
保留历史发布固定、不重算、不回退。G01/G02 原证据 tuple 仅追加实际文件路径，
其他 55 个 REQ 条目不变。

本批执行完整八文件后端切片与修改文件 focused Ruff；
历史后端实际为 779 passed／1 failed／零跳过，422.21s，exit 1，
保留 `contract-b-backend.log`，不改写为通过。
唯一旧失败 `tests/test_reset_analysis.py:1774` 把显式未知快照断言为 200，
而 B2 契约正确返回 404。控制器已批准合法的 B5 兼容测试范围扩展，
仅将最后缺失发布断言改为 404 与精确安全 JSON；此前 400／存储前校验／日期、
成员／角色及未知个股断言全部保留，无新增生产变更。
focused GREEN 为 1 passed／1.25s／exit 0；完整八文件最终门禁实际
780 passed／0 failed／0 skipped／443.96s／exit 0，
保留 `contract-b-backend-green.log`。旧断言阻塞已解决，B5 有限后端门禁关闭，
不代表全后端／全 PRD／C／D 验收通过；不把历史失败或跳过记为通过。
focused Ruff 与 73 行／引用／注册表校验通过；
外部 `contract-b-backend.log`／`contract-b-b5-ruff.log` 及失败定位见状态记录。
历史测试修改及失败后端完成后执行 proof generator，记录 500 源码／测试文件、
16 日志及聚合 SHA256，详见状态记录与外部 `contract-b-evidence.json`。
最终 GREEN 与文档校验后 proof 已刷新为 500 源码／测试文件、20 保留日志，
包含历史失败及最终通过日志；最终源码／测试聚合 SHA256 为
`25e226fca710afe29e2cd909f62084f4a769f4ea2bce8bbc5d64e412e66d13ff`。
文档校验 exit 0：57 唯一 REQ／16 唯一 EX／73 有效行与可解析引用；
八文件范围外 582 个基线文件哈希不变，无重复证据路径。
控制器已完成的稳定前端和源码门禁不重复：
TypeScript exit 0；47 个 Vitest 文件／1412 用例及 32 Node 用例通过且无跳过；
Chrome 搜索／排行两文件 29 passed（46.9s）；PRD checker
57 REQ／29 endpoints／29 examples／30 JSON；全 src/tests Ruff、
src compileall 均 exit 0。这些是各自有限范围的证据，不是完整后端或全 PRD。
Chrome 日志有 LHB 与 wordcloud 两个未 mock 代理 `ECONNREFUSED`，
不能称 console-clean、真实全网关或 C 验收通过。

AM01 已由 B2/B5 解决；AM02/04 由 B3 搜索／列表范围冻结；
AM03/05 由 B4 专用身份与公开模板／owner AI 边界冻结；
AM06 仅用当前保留日志及源码／测试聚合哈希，不重建已删除日志。
AM07 的 C 待办详见状态表：375/768/1024/1440 宽度和实际高度、
真实浏览器 200% zoom（不以放大文字代替）、对比度／焦点／Esc／返回滚动，
受控 60s／120s AI 模板与迟到草稿不覆盖已保存文本（真实延迟另列），
RT01-RT12 与 provider egress、隔离合成有效发布的真实 Caddy→API
题材／搜索／关联复核／引用和公开写入／私人路径／owner／撤权，
五构建变体身份／预算／懒加载／物理隔离，以及先于浏览器负载的
ubuntu-24.04 P95≤2s 性能门禁；隔离 Linux 完整 verifier 保留 90 分钟，
远程 CI 等待后续授权。PRD 四档仅明确 375/1440，
768/1024 是本次授权清单明确的中间宽度。
双向排名、指数历史归档与公开 reviewed-daily-AI 归档／发现仍为独立功能规格，
不是 C-only 验证；D 五项真实门禁继续 blocked。

## 工程覆盖

| 范围 | 实现状态 | 剩余条件 |
| --- | --- | --- |
| P0 基础、数据与确定性页面 | 六个主页面导航及独立个股详情、快照、校验、不可变发布、总览、消息、梯队、涨幅分析和监管已有实现；页面骨架与模块入口已恢复 | 真实授权输入、正式规则与完整历史覆盖 |
| P0 本地复盘 | 自选、笔记、冲突、草稿、历史验证和备份恢复已有实现；IndexedDB 为权威源 | 当前构建绑定的正式验收 |
| P0 AI | 四类任务、轮询、取消、审核、引用和数字校验已有实现 | ≥100 个唯一真实人工样本、无泄漏分组及真实模型质量验证 |
| P1 扩展 | 实时、分钟K/异动/分时资金流、龙虎榜、词云、轮动、偏好和白名单自然语言查询已有实现 | 真实来源、授权及延迟实测；自然语言查询保持白名单范围 |
| P2 B01 | PIT 回测、因子体检、walk-forward 和阶段条件调权的研究内核与界面已有实现 | 真实生产首版仅获批 FAC-R01 baseline；真实阶段序列与其他方法适配仍待开发 |
| P2 B02 | 本地模拟账本和 AI 同池对照框架已有实现 | 无真实评测输入时 ai_variant 固定 disabled，无该腿收益数字 |
| 数据采集 D02 | 采集、归档映射、标准样本、独立核验及持久化后授权检查已有实现 | 实际 iFinD 字段/响应配置、许可/账号/额度和首次获授权采集 |
| 部署边界 | CLI 管理输入，只读与私人应用/构建隔离，研究接口仅 owner 装配 | 正式环境、受信验收、公开许可与发布授权 |

PRD 附录 A 保留 57 项 REQ：47 项 P0、8 项 P1、2 项 P2。
29 项编号接口包含 25 项 HTTP 接口与 4 项 IndexedDB Repository 接口。
不以测试数或文件数换算总体完成百分比。缺授权、缺数据或缺规则时关闭相应能力，
属于现有契约行为，不代表真实生产条件已经满足。

逐项追溯见 [历史终验及追加说明](acceptance/reset-final.md)、
[研究验收摘要](acceptance/task27-research.md) 和
[全范围契约测试](../tests/test_reset_full_scope.py)。
2026-10-27 终验的 Task 27 未实现结论已由后续追加说明解除。

## 历史验证边界：2026-11-16／17

2026-11-16 研究阶段记录：后端 4256 passed、0 skipped、覆盖率 92%；
前端 Vitest 1237 与 Node 28 通过，五套 E2E 共 325 通过。
普通 GET P95 1.089s；研究并发三轮 P95 1.075 / 1.037 / 1.159s，均 ≤2.0s。
这些是历史本机与合成回归结果，不是当前提交或真实策略有效性的证明。
2026-11-17 供应商切片记录 Windows/Linux 各 412 项通过，同样不等于当前全量。

旧研究、供应商和整合阶段的日志、截图及 trace 已按文档精简要求删除；
历史原始文件可从 Git 查询。后续以当前提交重跑验证并绑定实际产物。
Task20/Task21 校验器直接依赖的证据仍保留。
尚未提供当前提交对应的远程 CI 和正式 `verify_reset.py --mode full`
受信执行报告，`automated_acceptance` 保持 `unverified`。

2026-11-17 页面结构恢复：主导航、研究工具入口、总览布局、无快照／单域失败骨架、
复盘分区和模拟草稿保护已按 PRD 重构。该阶段前端 Vitest 1238、Node 32、
新增结构 E2E 15 通过；五种构建及其隔离检查通过。全量前端回归发现的详情折叠、
移动命中区和无障碍问题已修复并完成相关复测。
详细范围与本机证据见 [页面骨架恢复记录](ui-prd-rebuild-plan.md)。
这项页面恢复不解除真实输入、正式规则或生产发布条件。

## 五项生产门禁

| 门禁 | 状态 | 必需材料 |
| --- | --- | --- |
| real_input | blocked | 真实许可、冻结输入、receipt/PIT/快照及实际内核运行 |
| official_rules | blocked | 官方归档、适用规则、方法与独立批准 |
| real_model_evaluation | blocked | ≥100 个唯一真实人工样本、真实输入输出与质量证明 |
| public_sharing_permit | blocked | 主体、用途、目标、受众、内容和数据的适用许可 |
| shadow_run_20_trading_days | blocked | 连续至少 20 个真实交易日的接收、实跑、复核与签章 |

接入条件统一见 [供应商契约](supplier-ingestion.md)、
[生产输入契约](production-inputs.md)、[独立证明契约](production-evidence.md)
与 [证明配置指南](proofs/README.md)。开发整合不构成生产发布。
