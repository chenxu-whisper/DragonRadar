# 远端分支整合与清理

用户授权将远端 `feature/*`、`fix/*` 分支合并到 `main` 后删除。
整合前本地 `main` 为 `23e26f0a757ac8c8d665f54ab31e54ebaefa87f1`，
远端 `main` 为 `974db40b8920e8ba745e22e7f5ef560919ee0bed`。

`feature/data-foundation`、`feature/task21r-task22` 和
`fix/baseline-ci-reds` 的提交均已包含在本地主工程。
`feature/task27-research-validation` 有 31 笔独立提交，其纯评分验证
实现与后续回测实现使用了同名模型、包入口和测试文件。

合并保留当前回测内核、成本模型、交易规则、私人 API 和全范围门禁。
将历史评分验证的七个模块整合到 `dragon_radar.research.validation`，
仅调整包地址；不改变算法、阈值和报告版本。历史回归另存为
`tests/test_research_method_validation.py`，纯包检查绑定新目录，
原来的“回测尚未实现”断言更新为当前“已实现且仅供研究”的断言。
历史分支设计和计划保持原文，本记录说明当前地址与阶段变更。

验证先执行迁移后的测试，确认缺失子包时失败；再整合模块，
执行评分验证、既有交易规则、情绪研究和全范围契约回归，
以及 Ruff、格式、compileall 和 PRD 契约。
实际专用 PG17 回归为 178 项通过、零失败和零跳过；
七个模块反向映射包地址后与原远端源码的 AST 完全一致。
首轮未配置测试数据库的四项跳过记录另存，不作为数据库通过证据。
合并提交保留研究分支为第二父提交，因此全部远端提交可沿 Git 历史追溯。
原远端分支 tips 的完整 bundle 保存在
`deliverables/remote-branch-integration-2026-11-25/`。

远端更新先发布 `main`，确认其包含全部四个分支的 tips 后，
再删除这四个分支，使用远端 tip 比较防止覆盖同期变更。
实际命令、日志、合并提交和删除后的远端核对记录保存于同一交付目录。
本次不变更版本号、迁移、生产配置或日常服务。
