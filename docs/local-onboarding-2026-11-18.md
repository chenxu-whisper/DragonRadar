# 本机使用体验：批次 A 交付记录

范围：批准计划 A1–A3。源码基线 `0feb928`；实施在项目外
`feature/local-onboarding` 工作树进行。主工作区原有文档修改保留。

## 行为与边界

- 无可读快照时，全局搜索入口禁用，并显示原因；有可读快照后恢复搜索、Esc 和焦点恢复。
- 两个 PowerShell 入口在 PowerShell 7 下访问私有状态前转交 Windows PowerShell 5.1，并传播退出码。
- Reset 所有者配对页提供本机取码与恢复指引；旧私人构建与公开包不装配该指引。
- 页面不读取保护文件、不签发配对码，不推断服务端未提供的拒绝原因。
- 本轮不变更生产数据、运行实例、版本与发布状态；B/C/D 继续使用原批准计划。

独立规格审查补充了锁定页退出的验收：配对框中新输入和先前拒绝提示也属于
待清空的临时状态；仅重建正文工作台不足以满足退出语义。配对操作说明继续
遵循手动输入、不经剪贴板或共享输出传递口令的现有约束。
A1／A2／A3 均已完成独立规格与质量审查；批次 A 的对应回归、
实际容器演练及整批最终复审通过。指定文件按冻结哈希同步，
文件清单与 SHA256 见项目外 `.temp/onboarding-copy-manifest.json`；
同步结果、原有文档与日常容器核对见 `.temp/onboarding-final-check.json`。

## 实测证据

| 检查 | 当前结果 | 说明 |
| --- | --- | --- |
| PowerShell 7 首次启动 RED | 已复现非零退出 | 隔离目录中 `CreateDirectory(path, ACL)` 重载不可用；未启动容器 |
| 跨宿主启动回归 RED → GREEN | 退出 1 → 退出 0、267 项断言通过 | 父流程独立复跑也为 267 项通过；5.1 父进程启动 5.1／7 子进程，原生 PATH 替身 |
| Ruff `src tests` | 通过 | `uv run --no-sync ruff check src tests` |
| 源码编译 | 通过 | `uv run --no-sync python -m compileall -q src` |
| 公开网关静态回归 | 34 通过、8 跳过 | `tests/test_reset_public_gateway.py`；不是完整容器验收 |
| 冻结契约与网关联合回归 | 115 通过、14 跳过 | `tests/test_reset_contracts.py tests/test_reset_public_gateway.py`；跳过项不记作通过 |
| 更新后的入口／文档契约 | 1 通过 | A2 实施后补跑 `test_reset_start_script_and_running_documentation_contracts` |
| 私有 ACL 回归 | 通过 | Windows PowerShell 5.1 `scripts/test_private_scripts.ps1` |
| TypeScript | 通过 | `tsc --noEmit --project web/tsconfig.json` |
| 前端完整单元与脚本回归 | 1412 + 32 通过 | `npm --prefix web test`，47 个 Vitest 文件 |
| 全局搜索浏览器切片 | 22 通过 | `playwright.reset.config.ts reset-ig11.spec.ts`，含 3 个新增 A1 用例 |
| 配对修正 RED → GREEN | 2 失败 → 5 通过 | A3 先验证手动输入指引缺失、锁定页退出残留，再修正 |
| 自定义入口地址断言 | RED 1 失败；8443／5187 各 1 通过 | 仅修正测试的端口禁用断言，校验链接与正文地址均等于当前 origin |
| 旧私人入口边界 | 9 通过 | `frontend-final-edges.spec.ts`，修正后复测 |
| 所有者完整浏览器切片 | 36 通过 | `playwright.ai.config.ts`，含 5 个 A3 用例与既有 AI／会话回归 |
| 五种构建 | 通过 | 预算、研究懒加载、公开包无 owner/AI 请求与配对控件，旧私人包无 Reset 取码指引 |
| 独立 Compose 首次／重复启动 | 通过 | PS7 首次、5.1 重复；所有者身份、配置与 CA 一致，7 服务就绪 |
| 受信 HTTPS 与入口动作 | 通过 | 显式 CA 校验、单次配对及复用拒绝、读取、PS7 Certificate／Pair／Revoke |
| 实际公开 Caddy 页面 | 通过 | 无快照搜索禁用、原因可见、无打开弹窗；搜索与私人 API 请求均为 0 |
| 演练清理 | 通过 | 仅清理唯一测试项目的容器与卷 |

五种构建入口大小（字节）：default 306070、sharing 308583、reset 313221、
private 295409、owner 310739；sharing 预算 320000，其余 315500。
纯 LOCAL Reset 包允许本地 IndexedDB 复盘标签；其所有者接口／配对模块隔离
与公开分享包的标签隔离采用各自既定规则，不放宽入口预算。

所有者完整切片在运行时代码修正后执行；随后只修正了上述端口测试断言，
在 8443 和默认 5187 分别补测该用例，未再次重复整个切片。

本批证据统一在项目外 `.temp/onboarding-*`；跨宿主回归使用项目父目录
`.temp` 下的 `reset-start-test-*`，退出时只删除本次目录。
实际 Compose 夹具保持入口脚本字节不变，
PATH relay 仅将固定项目名映射为唯一测试项目；夹具 Compose 改用空闲子网、
当前源码构建路径与专用 `dragon_test` 数据库，独立创建并清理卷。
HTTPS 读取使用该夹具的显式 CA 校验；模拟 HTTP 浏览器用例与此分别记录，
不将忽略证书错误称为受信 HTTPS 验收。

实际演练中另发现测试宿主环境问题：IDE 启动的 Python 继承 PowerShell 7
模块路径，直接启动 5.1 时 Security 模块类型定义冲突。最小对照验证清除
继承的 `PSModulePath` 后模块加载正常；仅夹具改用各宿主默认模块路径，
入口脚本、ACL 和运行实例保持原样。

公开浏览器探针曾把 `/assets/search-index-B1sz-TQl.js` 算作搜索请求，
现按 `/api/` 路径计数。最终报告同时保留该静态资源与零搜索 API 请求的证据。
完整演练报告：
`E:/ByteDance/Projects/Code/.temp/onboarding-compose-a3f5502ba3/report.json`。
该报告绑定入口脚本与三个前端运行时文件的 SHA256；HEAD 仍为源码基线，
不能仅凭 HEAD 把本轮未提交代码当作既有提交的验收。

本记录的门禁范围为批次 A。115 项联合回归中的 14 项跳过、尚未执行的完整
Linux verifier 与远程 CI，均不计入当前版本完整工程验收；后者继续归属批次 C。

## 后续授权的提交与运行更新

用户在批次 A 源码交付后另行授权提交并更新现有实例。代码提交为
`ed7553ac383312f34957565c19100ba9ed1b8b0d`，通过 PowerShell 7 执行正式
`scripts/start-reset.ps1`，退出 0，七个服务就绪。
公开／所有者 Caddy 与所有者 API 已重建；公开 API 和两个 worker 的后端镜像
未变化，Compose 复用现有容器。数据库容器、持久卷、配置、CA 和所有者身份保留。

公开健康、快照列表及首页均返回 200；所有者 HTTPS 使用显式 CA 校验通过，
未配对会话返回 401。实际 Chrome 保持正常 TLS 校验，确认公开页搜索禁用和
原因可见、搜索／私人 API 请求均为 0；所有者页取码命令、当前入口链接、
五分钟说明可见，空码提交禁用。本次未签发配对码或撤销现有会话。
运行证据位于项目外 `.temp/onboarding-runtime-1f04ae97a8/report.json`；
本节是后续运行记录，不改写先前隔离演练的源码基线或完整验收状态。
