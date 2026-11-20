# 批次 C：C0/C1 工程验收

执行日期：2026-11-19。本报告保留已批准计划的 C0/C1 证据；
C2 已在独立工作树完成六项有限验收，见 [C2 执行记录](contract-c2-2026-11-19.md)，
C3–C5 与 D 仍待各自执行。本报告的 C0/C1 历史证据保留。
C0 五项与 C1 八项验收已关闭，独立规格／质量及最终整体一致性复核
均为 PASS；两个最终运行门禁已通过。
完整 `automated_acceptance` 保持 `unverified`。

## C0 源码与日常实例冻结

源码基线为 `0cb91c37d42faecda1b384f40b1f972aaebac4c3`，
日常应用运行源码为 `245b7355659ac74265f66e5d1e3adf040d9ae457`。
独立工作树为 `E:/ByteDance/Projects/Code/.temp/DragonRadar-ContractC`，
分支为 `feature/contract-c-20261119`。

`E:/ByteDance/Projects/Code/.temp/contract-c-baseline.json` 冻结 591 个
tracked/untracked 文件的原始字节 SHA256、实际 Git 状态及七个日常服务的
容器 ID／镜像 ID／启动时间／健康状态／挂载身份。隔离工作树采用逐文件
并发哈希检查后复制的基线字节，保留既有文档修改和 CRLF。
日常配置、owner 本机文件、CA 与 owner 身份只读取哈希，不归档秘密内容。
最终回传前再次检查基线，冲突文件不覆盖。

锁文件：

- `uv.lock`：`815e99a6fa53506a1a57c7c8e3dbd2868c56023f2eeb1a8265865eb1554f1228`
- `web/package-lock.json`：`a9e6ffda8ba646e670c2e57156e08ac3845821e3d62ecf1d9600727fefd9bfdd`

已检查工具为 Docker Server 29.9.2、Compose 5.6.1、Node 24.14.0、
uv 0.8.2；Playwright 锁定版本为 1.63.0。
Linux 验收环境使用 Docker Desktop Linux 容器，不将宿主 Windows 写成 Linux。
已下载并检查 `mcr.microsoft.com/playwright:v1.63.0-noble`：
实际 Linux／amd64 镜像 ID／RepoDigest 为
`sha256:eff16c30e6f3f4af0a03fa4b706120d5e9b0891c344a27d64559aff5900a4a27`。
在 `--network none --rm` 容器内实际核对 Ubuntu 24.04.4 LTS、
Node 24.20.0、Chromium revision 1243／Chrome for Testing 153.0.8010.12。
镜像及工具输出分别归档为 `contract-c-browser-image.json`、
`contract-c-browser-tools.json`；该版本探针不证明浏览器 TLS 或页面链路。

## C0/C1 执行证据

### 当前最终门禁结果

控制器实际执行两个计划入口，均 exit 0：

| 入口 | 实际 run ID | 结果 |
| --- | --- | --- |
| `npm --prefix web run test:contract-runtime` | `45252-1795068624675-c8683e1c` | passed；清理 removed，容器／卷／网络残留为空，秘密文件已删除 |
| `npm --prefix web run test:e2e:production-isolation` | `26524-1795069437461-7b30f253` | passed；相同清理结果；仅原有隔离探针范围 |

证据根目录分别为项目外 `.temp/reset-contract-runtime/<run ID>/` 和
`.temp/production-isolation/<run ID>/`。`manifest.json` 与
`commands.jsonl` 的 SHA256：

| 运行 | manifest | commands |
| --- | --- | --- |
| C1 | `f357b58a4740a7c79e80898532878fc49704821151e5629dfba5209996b7a8fe` | `b35354d9d32aa2ab57db0c80e339670b37a6ceba0c5fe4303d209342f3edb08f` |
| 原有隔离 | `f9288b18c340c4499d2b119dbed777b56def7ed016a77dc0a33c9ba8d2338c4f` | `e536433d9aaa4e8787027b44436a832c7619bc2b6f408be1999694dfdd94f64d` |

C1 实际数据库为可丢弃 `dragon_test`／`public`，迁移
`0014_research`。全部 CLI receipt、11 份合成输入哈希、来源版本和可用
时间、镜像 ID／digest、静态资源身份均保留在该运行证据中。
P1 为 `f8e05569-35ce-4adc-93c6-cb44a7fdf4ea`，P2 为
`9747cb56-f5fa-4e56-9d19-bd98ea48b867`；日期均为 `2026-09-08`，
实际证券／题材分别为 `SYNTH-C1-1`／`SYNTH-SECTOR-1` 和
`SYNTH-C1-2`／`SYNTH-SECTOR-2`。页面实际打开原证券、题材、文章与
归档引用证据区域，保持 PUB／日期。owner 复核产生独立审计，仅 P2
读取新复核状态；P1 复核前后及撤权后的原始归档哈希均为
`7ef423242ce7ea5fd07822696e16db93ab4a1f13e12fdc36c61f72643715432a`。
来源和 owner 撤权后的可见内容／目标均清空。

专用 Linux Chromium `153.0.8010.12` 在新鲜信任前实际拒绝证书
`ERR_CERT_AUTHORITY_INVALID`，随后安装本夹具系统与两处 pwuser NSS
信任，在真实 owner-Caddy 网络命名空间通过精确
`https://localhost:8443`。CA SHA256 为
`c1baa59d09a7278cd5337610e99e5b29efeda3ac39b56b2a94bf28a46ab7a3f9`。
没有绕过证书、修改宿主信任库或替换 API 响应。原有 production-isolation
使用 Edge `157.0.4341.60` 及既有忽略证书配置，其通过不作为 C1 受信 TLS
证据。两个原有与新增范围分别保留。

实际公开 54 个、owner 143 个 JS/CSS 均为 status 200，MIME 和 SHA256
已核对，首页 HTML 也保留状态／类型／哈希。公开构建旗标为
`VITE_RESET_WORKBENCH=1`、`VITE_PUBLIC_SHARING=1`；owner 为
`VITE_PRIVATE_REVIEW=1`、`VITE_RESET_WORKBENCH=1`、
`VITE_PRIVATE_AI=1`。公开保留 1 个、owner 保留 5 个精确预期 HTTP
拒绝及匹配控制台错误；没有非预期页面／资源采集／失败请求错误，
不宣称控制台零错误。LHB 默认拒绝须匹配归档证券／日期／发布时点、
页面阶段及五个唯一参数；重复或额外参数不能放行。

Windows 实际 env／override／配对文件具有本机逐文件 ACL 证明。
owner-init 实际生成文件数为 0，`files=[]`、
`perFileAclApplicable=false`；不伪造生成文件来冒充逐文件 ACL 验证。
2026-11-19 06:27 UTC 最终运行后核对日常七服务、HEAD、保护配置／CA、
owner 身份均未变，主工作区 591 个冻结文件原始字节全部一致。

### 最终回归及源码身份

当前 530 个源码／测试／脚本文件聚合 SHA256 为
`9ca37d5fcaadd67e9823f3bf16b2578e9b5d86717cb5ed86f55a8b8b5b58b075`。
C1 运行时源码聚合为
`c48b24d83f6a8a939f9b455a5e8c322b387fa92945c3d7905e95f3478d34b4ae`；
之后仅两个原有生产隔离脚本／测试文件同步了消息复核探针，
共享 C1、产品源码与锁文件未变。两份独立源码清单分别保留为
`contract-c-controller-c1-source.json` 与
`contract-c-controller-final-source.json`。

稳定前端完整门禁为 47 文件／1412 Vitest／96 Node；随后验收脚本修复
由聚焦检查与最终全部 146 个 Node 用例覆盖，均零失败／跳过。
TypeScript exit 0；无数据库读取契约回归 171 passed／24 deselected，
这 24 项数据库集成未执行，不计为通过。全 src／tests／scripts 和 C1
Python 夹具 Ruff、compileall exit 0；PRD checker 为 57／29／29／30。

| 控制器日志 | SHA256 |
| --- | --- |
| `contract-c-controller-runtime-final.log` | `66d7d4cf8d3f47a59ec4788b98c85a821c03d0c4bcb5c83d4a5cc007ae4fb092` |
| `contract-c-controller-isolation-final.log` | `6bffdeb22961098d08fb15288a15fff0ce9cff4eb4c78aadd2671b6d95503db6` |
| `contract-c-controller-node-closed-final.log` | `f44b0c5d80e3996bf4d14c71d91888a7f6ef49f34b1cc9008b4a6d57f1d3b0e7` |
| `contract-c-controller-frontend-final.log` | `4bac36d1b88680682dea659669139cfe8cf6c73edc5a27609b8bf401c65bdc59` |
| `contract-c-controller-pure-backend-final.log` | `7e0c078d87532e58a1a153442b815479b4e95c6c216fd0dbdb2048e587314a30` |

其他静态检查、失败到通过日志与独立复审 SHA256 统一保留于
`E:/ByteDance/Projects/Code/.temp/contract-c-evidence.json`，不归档秘密文件。
最新独立复审均为 PASS：`contract-c-c1-spec-recheck.md`、
`contract-c-c1-duplicate-spec-recheck.md`、
`contract-c-c1-duplicate-quality-recheck.md`、
`contract-c-production-probe-spec-check.md` 与
`contract-c-production-probe-quality-check.md`。原有探针最终质量复核
没有剩余可行动问题。
最终[整体一致性复核](file:///E:/ByteDance/Projects/Code/.temp/contract-c-final-integration-review.md)
为 PASS：实际证明、530 源码身份、有限测试数量、四份文档和
22 文件回传范围一致，未发现剩余可行动问题。
文档随后仅将本复核标记为关闭并补充回传记录入口，未改动已验证源码。
控制器回传限定为 18 个变更源码／测试对象加 4 份文档；
未改变的 `production-isolation-support.mjs` 和生成的
`web/tsconfig.tsbuildinfo` 不在清单中。回传前再次核对全部 591 个
冻结文件；执行后的逐文件 SHA256、其余文件及 Git 暂存区核对结果
单独记录于项目外 `.temp/contract-c-return-verification.json`。
新增规格缺口（公开 LHB 分类过宽）及质量缺口（重复参数取首值）均
建立失败回归并完成独立重审。随后原有生产隔离脚本因把现已允许的
消息复核 POST 预期为 404、且省略 JSON Content-Type 而实际得到 400；
该失败实例 `38212-1795068983440-e9fa9ab7` 已清理、秘密已删除。
仅同步探针为配对会话、JSON `{}`、无 CSRF，严格断言
403 `FORBIDDEN`／`Access denied`，其他边界拒绝不变。失败历史保留为
`contract-c-controller-isolation-prealignment.log`；最终复验通过。

以下为执行期间的阶段记录；其中“待完成”仅描述当时状态，
最终状态以上方门禁与复审关闭记录为准。

### 基础检查与失败历史

在隔离工作树执行的 Ruff、compileall 和 TypeScript 均为 exit 0。
PRD checker 实际输出为 57 REQ、29 endpoints、29 examples、30 JSON。
初次完整前端检查为 1410 passed／2 failed：工作树未提供 `.venv`，
两个真实 Python 夹具进程因 ENOENT 失败。随后为工作树提供锁定依赖的
`.venv` 与 `web/node_modules` junction，并明确设置隔离源码／测试
PYTHONPATH；对应聚焦回归为 59 passed。首次聚焦重跑误用了
`web/tests` 路径过滤器，返回无匹配文件／exit 1；更正为 `tests/unit`
后通过。以上失败日志均保留。完整前端重跑现为 47 个 Vitest 文件、
1412 passed，加 94 个 Node 用例全部通过、零失败／跳过、exit 0。
日志为 `contract-c-frontend-final.log`，SHA256
`a00e960d244f6e4cbea4186922c003ce86d6a6346f4076426c4ac80274a77900`。
此运行覆盖当时的脚本源码，若后续修复改变相应对象须重跑其相关检查。

原始日志目录为 `E:/ByteDance/Projects/Code/.temp/`：

| 日志 | SHA256 |
| --- | --- |
| `contract-c-ruff.log` | `a4443afdcfb6d7363adb285762515ccf7cf50473b1a05c20c1a50f6bed4d26b0` |
| `contract-c-compile.log` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `contract-c-prd.log` | `6f4e938d03dbfb2870712a9e0ab3cf7e611cc32b2643110a7791b13cf7944ed4` |
| `contract-c-tsc-c0.log` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `contract-c-frontend-c0.log` | `681e192a08da40b88fe287952f397b6fff43e5a3d1e7449b4888e329141c4ff9` |
| `contract-c-python-fixture-green-corrected.log` | `0aade5b745343d8a82d63dabfbb1bac2e3b7c603b4f63ae71735d624936911a0` |

C0 初次规格复审通过，质量复审指出 production 浏览器子进程环境未
隔离、清理身份／残留未验证，以及成功输出早于清理且证据目录被删除。
集中修复同时补齐 JSON 秘密字段脱敏及容器生成 owner 文件的
宿主 ACL 证明入口。修复者保留 43 个基线通过、10 个新增回归失败和
最终 53/53 通过的证据于 `contract-c-c0-tdd-20261119-fresh/`。
控制器在补丁完成前检查为 52 passed／1 failed（production 命令记录
入口尚未接入）；完成后重新执行为 53 passed／0 failed／0 skipped，
日志分别为 `contract-c-c0-quality-controller.log` 与
`contract-c-c0-quality-controller-green.log`。六文件语法检查通过。
新的独立规格／质量复审尚未关闭；容器生成文件的实际 ACL 证明及
有效发布浏览器正向验收尚未执行。

规格重审发现运行 manifest 仍将 Docker／Compose／Chromium 写为
`pending`，最终状态却可为通过。已补齐实际工具版本、所选浏览器
channel／版本、锁文件身份及不完整元数据的失败回归；仅 HTTP transport
的 runner 明确标记浏览器不适用。元数据修复者保留 53 个基线通过、
9 个新增回归失败及最终 64/64 通过证据于
`contract-c-c0-metadata-fix/`；控制器复核为 64 passed／0 failed／0 skipped，
日志 `contract-c-c0-metadata-controller-green.log`。规格复审通过，独立
聚焦验证为 14/14 加六文件语法通过；质量复审尚未关闭。
报告中的基线工具清单不能替代每次实际运行清单中的身份绑定。

最后一轮 C0 质量复审的独立检查为 48/48 聚焦测试、零跳过及六文件
语法通过，但发现配对码可进入 Playwright `fill()` 失败诊断：
既有脱敏只注册数据库密码，未注册实际配对码，删除配对文件不能
清除错误日志与 manifest 中的副本。该 P1 问题已交由独立修复者建立
失败回归并集中修复。六种失败诊断场景先失败后通过，修复者聚焦
58/58 与六文件语法检查通过；控制器完整 C0 三文件检查为
70 passed／0 failed／0 skipped／exit 0，保留
`contract-c-c0-pairing-controller-green.log`。实际配对码在浏览器使用前
注册于仅内存秘密集合，所有诊断出口及命令、manifest 写入共用脱敏；
独立规格与质量复审均通过，未发现该修复的剩余问题。
C0 基础设施代码复审已关闭；实际容器文件 ACL、数据库／schema、
浏览器信任和清理身份证据仍随 C1 演练补齐，不提前勾选全部环境验收项。
C1 实施已开始，实际运行尚待完成。发布绑定／归档引用断言先因缺少
实现而失败，新增实现后的三文件回归为 73 passed／0 failed／0 skipped。
两组 61-session 合成输入生成器先因缺少生成文件失败，随后聚焦
1 passed／0 failed／0 skipped。这些脚本结果不替代实际发布、网关或浏览器验收。

| C1 初始回归日志 | SHA256 |
| --- | --- |
| `contract-c-c1-bindings-red.log` | `9e1bedffed3139b59cf34b074d47bde476300f32324f584ab3aea24887dcec26` |
| `contract-c-c1-bindings-green.log` | `a9b89050ca84b8ea1f00d50b07b089b9604e65a8a7b26e8dab7c59595a94ec8f` |
| `contract-c-c1-generator-red.log` | `106b1e120c02648594ca802cbb96a2de01dfb5c492581b6c3b8bc13828830ca7` |
| `contract-c-c1-generator-green.log` | `34256aca503e2972f08c81c511a98cc7afbdcfb91b5cad3a3c104647d6c23b3c` |

首次真实 Docker 演练实例为
`dragon-radar-contract-51872-1795062085872-ff530fe0`。实际 CLI 已生成
第一份发布 `d023026a-69e0-4e03-ac29-a2a611693e94`，随后夹具归档读取
在 `domains["topic"]["payload"]["topic_details"]` 处出现 `KeyError`，
整体结果为 failed；该失败不能记作 C1 正向通过，浏览器证据尚未完成。
manifest 记录清理状态 removed，残留容器／卷／网络列表均为空。
失败日志、manifest 和命令记录保留在项目外 `.temp`：

| 首次演练证据 | SHA256 |
| --- | --- |
| `contract-c-c1-docker-attempt-1.log` | `f673d5e8a3271bab53f21834bd7e24ba0c8739b9e78b75035325b37c61242587` |
| `reset-contract-runtime/51872-1795062085872-ff530fe0/manifest.json` | `0e712d8e2d79fc55cdf314fba2f6a01a5495b8f2d2b36b074e37281649144418` |
| `reset-contract-runtime/51872-1795062085872-ff530fe0/commands.jsonl` | `4b32bf4f66e89ccafce418dc6ef6b53ce59d4479cc6079387c8ebb4fc8a5ec6e` |

第二次演练实例为 `dragon-radar-contract-51044-1795062319795-e9f0d1d3`。
真实 public 浏览器在导航后读取静态资源响应体，触发
`Network.getResponseBody: No resource with given identifier found`，
随后浏览器会话关闭，消息详情断言未完成。该运行整体 failed；
不能从被中断的断言判定应用消息详情存在缺陷，也没有完整 TLS 通过证据。
manifest 再次证明清理 removed、容器／卷／网络残留为空、
`secretsRemoved=true`。归档读取修复的聚焦回归为 1/1，夹具 Ruff
检查通过；后续包含数据库/schema、引用和诊断脱敏的脚本回归为 83/83，
仍不替代完整运行结果。

| 第二次演练证据 | SHA256 |
| --- | --- |
| `contract-c-c1-docker-attempt-2.log` | `ee86883bc4660ee1f04503168dd53f5fc83a4266499d81bd8a733188117133f7` |
| `reset-contract-runtime/51044-1795062319795-e9f0d1d3/manifest.json` | `028aaefb89cc087a04dfd5f576ce9d93e526c8d41c0539f9c249c4a7d58d031a` |
| `reset-contract-runtime/51044-1795062319795-e9f0d1d3/commands.jsonl` | `04d28844a74f35be57e98a1ccc9583e3fb5518bf6ddb49a3dfe3d9f8e96ba98a` |

第三次演练实例为 `dragon-radar-contract-68952-1795063660652-91ef57b9`。
实际查询数据库为独立容器的 `dragon_test`，schema 为 `public`，
迁移为 `0014_research`。public 浏览器在显式消息页导航前刷新响应证据时，
五个异步 JS 资源仍触发 `Network.getResponseBody` 无标识符错误。
增加 `networkidle` 与响应队列排空未解决这一采集问题；整体仍为 failed，
owner 严格 TLS 未执行。失败作为后续定位依据，未降格忽略。
manifest 证明清理 removed、容器／卷／网络残留为空且秘密文件已移除。

| 第三次演练证据 | SHA256 |
| --- | --- |
| `contract-c-c1-docker-attempt-3.log` | `ee86883bc4660ee1f04503168dd53f5fc83a4266499d81bd8a733188117133f7` |
| `reset-contract-runtime/68952-1795063660652-91ef57b9/manifest.json` | `9bb1952079e217489c3581defb5b9019938630d57efd43492b993269c8b6bac6` |
| `reset-contract-runtime/68952-1795063660652-91ef57b9/commands.jsonl` | `ffb9a9687ac982e4b160eef8ee1fb690fcaa5d33dc33a22c1cc20598e02fdb7a` |

第四次实例 `dragon-radar-contract-24916-1795063949039-815eae92`
已完成 public 证券／题材／文章可见目标断言和资源响应采集，但最后将
`/api/lhb` 的空错误正文解析为 JSON，出现 `Unexpected end of JSON input`。
public Caddy 的精确只读 allowlist 不包含 `/api/lhb`，该补充查询走默认
404 空正文；后续须精确核对这一预期拒绝，而不是泛化允许全部 404。
整体 failed，owner 链路仍未到达。实际清理已移除全部本次容器、卷和网络。
前一轮资源采集错误仍须确认具体成因，不由此次局部进展推定已解决。

| 第四次演练证据 | SHA256 |
| --- | --- |
| `contract-c-c1-docker-attempt-4.log` | `ee86883bc4660ee1f04503168dd53f5fc83a4266499d81bd8a733188117133f7` |
| `reset-contract-runtime/24916-1795063949039-815eae92/manifest.json` | `379d8bcf5b12afe693a46e34b4082916f6953ab34d241c22d0c48696aa190748` |
| `reset-contract-runtime/24916-1795063949039-815eae92/commands.jsonl` | `f94f2856dc842c0587a05e3c125a65e7b244ce47d85a8c2938ed12c5be137d3c` |

第五次实例 `dragon-radar-contract-4692-1795064200181-7afc4c5f`
再次整体 failed，清理 removed。精确分类 `/api/lhb` 后，发现
`/api/market/sentiment/research` 对合法发布
`67fb73ac-b632-41d9-a957-02e61833c3ba` 返回 404，
JSON 为 `{"code":"NOT_FOUND","message":"Resource not found"}`。
请求所带日期为 `2026-09-08`，PUB 与实际 CLI receipt 完全相同；
公开 Caddy 精确允许该路径，现有路由也已定义。此响应不能作为预期
错误放行。根因已定位为 `src/dragon_radar/http/public_app.py` 的
`PUBLIC_READ_PATHS` 遗漏现有 `/api/market/sentiment/research`。
已授权仅补齐这一既有公开只读路径，并建立发布绑定／未知发布的失败回归；
实际修复与容器复验结果待记录。owner 链路仍未完成。

| 第五次演练证据 | SHA256 |
| --- | --- |
| `contract-c-c1-docker-attempt-5.log` | `ee86883bc4660ee1f04503168dd53f5fc83a4266499d81bd8a733188117133f7` |
| `reset-contract-runtime/4692-1795064200181-7afc4c5f/manifest.json` | `1d499de5f37cc803894d5eb26a179fdb61e514b3e6806067581e5d8e6eca28d1` |
| `reset-contract-runtime/4692-1795064200181-7afc4c5f/commands.jsonl` | `abb757ad015d58b7551b334e0862adcfe9d963f0352af81a5ada3e9fd3524279` |

上述公开只读路径已做一行补齐，新增三项 HTTP 回归先为 3 failed：
合法归档 PUB 和旧归档 PUB 均实际返回 404。测试的不可变对象检查
一度因 `deepcopy(mappingproxy)` 失败，另有脚本源码格式匹配失败；
这些中间失败保留，不以名为 `green` 的日志推定通过。
更正后 `contract-c-c1-public-bound-route-corrected-green.log` 为
3 passed／零跳过，涵盖归档 research 正常返回、旧归档
`materialization_unavailable`、未知 PUB 404，以及归档读取后保持不变。
该日志 SHA256 为
`de4dcba45a39f379c5075f5e0a4b576b5eb57f258020ed72e1e202f8a50f025c`；
对应发布绑定失败日志 SHA256 为
`701386402f6f6d9c6137d18594c03d39ec59c99d8d42b814404cadebbebb1313`。
最新聚焦脚本日志 `contract-c-c1-attempt-6-node-corrected-green.log` 为
61 passed／零失败／零跳过，SHA256 为
`af42242580ee3482dbf3fa774a39bb1d695b83adc1a08deed2101d2e257afb1c`。
第六次实例 `dragon-radar-contract-11924-1795064734638-4b8b8522`
整体 failed，清理 removed、容器／卷／网络残留为空、秘密文件已移除。
本次合法发布的情绪研究请求不再出现前一轮 404，但
`GET /api/news/wordcloud?date=2026-09-08&snapshot_id=20b6719f-e0b4-49a3-bfdb-0a9841b8d709&top_n=40&type=all`
返回 400 `INVALID_PARAM`。根因为现有词云静态路径同样被
`PUBLIC_READ_PATHS` 遗漏，公开应用只留下 `/api/news/{article_id}`，
使 `wordcloud` 被当作文章 ID 校验。已授权补齐该既有公开读取路径与
绑定发布的失败回归，并集中核对同一边界；不把该 400 放行。
owner 严格 TLS、复核、引用和撤权仍待实际通过。

| 第六次演练证据 | SHA256 |
| --- | --- |
| `contract-c-c1-docker-attempt-6.log` | `38b85c18ef4b6d53ccda1354a5e49fefc0f2df3ca770bf48cf77553a3e582ff0` |
| `reset-contract-runtime/11924-1795064734638-4b8b8522/manifest.json` | `4af8d80b24c8fed66c134427045346bbb4995667ce732cf31552829f26f7266c` |
| `reset-contract-runtime/11924-1795064734638-4b8b8522/commands.jsonl` | `0a9a03ea68eebfe1b7c722ffed4bedabc4b0ebd6607122d331eca3b533ccea9e` |

两处公开静态路径修复的联合回归最终为 5 passed／零跳过：
增加损坏研究归档 503 `DATA_UNAVAILABLE` 与词云实际归档／未知 PUB
边界。第七次实例 `dragon-radar-contract-56824-1795065151074-1b32bb88`
公开浏览器通过，保留合法发布 `127432e9-498b-4493-9104-5747ba54729e`
下真实证券、题材、文章可见目标和 54 个实际 JS/CSS 响应。
公开控制台仅出现精确 `/api/lhb` GET 的预期空正文 404；
未宣称控制台零错误。owner 新鲜浏览器在安装信任前实际返回
`net::ERR_CERT_AUTHORITY_INVALID`，Chromium 为 `153.0.8010.12`。
随后的 NSS 证书安装进入重复密码提示，stderr 约 33.5 MB，
触发 `spawnSync docker ENOBUFS`；第七次整体 failed。
这不是信任后成功，也不证明 owner 配对、复核或引用。
已授权仅修复可丢弃浏览器内的非交互 NSS 安装与失败边界，
不扩大缓冲区、不跳过证书、不改变宿主信任库。
清理 removed，容器／卷／网络残留为空，秘密文件已移除。

| 第七次演练证据 | SHA256 |
| --- | --- |
| `contract-c-c1-docker-attempt-7.log` | `14a4cee75d04ca16c71885d14899a59247477fe3a9b558f0249dbb32a24311cf` |
| `reset-contract-runtime/56824-1795065151074-1b32bb88/manifest.json` | `a073c95d6511068fafb815a5d0ab5828bafbc847306e7f7dab32f0a4fb6dad85` |
| `reset-contract-runtime/56824-1795065151074-1b32bb88/commands.jsonl` | `1752299acb3265b7a57f42e67436cc1269c14cde7c6e747151d97720ace90548` |

清理前只读身份检查 `contract-c-daily-invariants.json` 已确认 HEAD、
七服务容器／镜像／启动时间／挂载／健康、保护文件／CA 哈希和 owner
身份均与冻结值一致。实际演练结束后须再次检查。
2026-11-19 04:10 UTC 再次检查通过，同时 591 个冻结文件的主工作区
原始字节全部一致；此结果仍不是演练结束后的最终检查。
2026-11-19 04:49 UTC 再次只读核对日常实例：HEAD、七服务身份、
保护文件／CA 和 owner 身份全部未变；591 个冻结文件主工作区原始字节
也全部一致。第三次演练随后失败并清理，本报告尚不记为最终通过。
2026-11-19 05:12 UTC 在第六次失败清理后再次只读核对：七服务、
HEAD、保护文件／CA、owner 身份均未变，主工作区冻结文件无字节变更。

第八次演练已启动，实例 ID 为 `59988-1795065586876-2a4aa04b`，
日志为 `contract-c-c1-attempt8.log`。本次仅在安装信任前的浏览器关闭后，
重建两个可丢弃 pwuser NSS 数据库；初始化、导入和检查均使用显式
空密码输入及 15 秒超时／2 秒终止宽限。失败回归及最终 62/62
两文件 Node 检查保留为 `contract-c-c1-nss-red.log` 与
`contract-c-c1-nss-green.log`。不扩大 stderr 缓冲、不绕过 TLS、
不改变宿主信任库。第八次最终 failed，清理 removed、容器／卷／网络
残留为空、秘密文件已移除。公开浏览器再次完成，owner 实际信任前拒绝
及信任后配对检查点分别保留为 `c1-pretrust.json` 和
`c1-checkpoint-paired.json`。随后缺 CSRF 的 POST 预期
`csrf_forbidden`，实际得到 `FORBIDDEN`，在 reviewed 检查点前退出。
完整静态核对确认 owner 写请求先验证精确 Origin，再验证 CSRF；
消息复核／AI 路径同时将 Origin、会话和 CSRF 拒绝统一映射为
403 `FORBIDDEN`／`Access denied`。夹具的 APIRequestContext POST
未设置 Origin，实际未到达 CSRF 门禁，而且误用了 session 路径的
`csrf_forbidden` 错误码。已授权仅给该 owner 写入探针设置真实精确
Origin，继续省略 CSRF，并按现有消息复核契约严格断言
`FORBIDDEN`／`Access denied`。未修改生产安全逻辑。
本轮没有 owner 完整页面／复核／引用／撤权通过证据。

第九次演练实例为 `32740-1795066021585-f29a5c1e`，最终 failed，
清理 removed、残留为空、秘密文件已移除。实际 CLI 发布为 P1
`740f9733-5d58-440f-ad8d-02ba9e308993` 和 P2
`1114c2b6-0235-40cd-beed-6ab01bc9a4e8`；证券／题材分别为
`SYNTH-C1-1`／`SYNTH-SECTOR-1` 与
`SYNTH-C1-2`／`SYNTH-SECTOR-2`。
owner 实际复核完成，P1 复核前后原始归档 SHA256 均为
`64d8cdce6a58311207b90726c58208116d2748f2a6b6a81cbffe16e5f876f33c`，
P2 读取接受且生效状态。真实引用来源高亮及对应 evidence GET
已到达 citation-opened 检查点，status 为 ready、绑定 P1。
来源撤权后的页面清空断言通过，随后直接 search GET 的错误码断言
失败：预期 `FORBIDDEN`、实际 `source_forbidden`。完整静态核对
确认 owner 应用对来源拒绝使用 403 `source_forbidden`／
`Source access denied`；已授权仅更正夹具的精确错误正文及对应
阶段／路径分类。owner 撤权及最终全部网络断言仍未到达。

控制器扩大公开网关／情绪研究／词云的无数据库回归，
`contract-c-pure-backend-pre-alignment-red.log` 为 169 passed／1 failed，
24 项依赖数据库的集成测试明确 deselected，零跳过；不将其记作全量
后端或集成通过。唯一失败为主工作区原有的
`test_unpublished_snapshot_is_empty_envelope_and_post_rejected`，仍预期
未知显式 PUB 返回 200，而批次 B 已批准并实现 404。
已授权仅同步该测试的未知 PUB 名称及精确 404 错误正文断言，保留
POST 403；生产语义不变。更正和后续实际结果待记录。
该测试更正后，控制器实际重跑为 170 passed／24 deselected／零跳过，
exit 0，保留 `contract-c-pure-backend-final.log`。这 24 项仍是未执行的
数据库集成范围，不计为通过。Python 全源码／测试／脚本及 C1 夹具的
最终 Ruff 和 compileall 均 exit 0；PRD checker 为
57 REQ／29 endpoints／29 examples／30 JSON。
第十次演练已启动，实例为 `62764-1795066353902-1d36abe0`，
最终 failed，清理 removed／残留为空／秘密文件已移除。
本次 owner 复核、P2、引用、来源及 owner 撤权检查点均已到达，
最终网络断言发现 owner 词云 422 `invalid_request` 三次，以及精确
龙虎榜 GET 的空正文 404 两次。owner 网络诊断保留 143 个实际资源，
全部 status 200，资源采集错误、页面异常和失败请求均为空。
不能据此跳过最终错误断言并记为通过。

词云根因为 `src/dragon_radar/owner/app.py` 的
`_RESET_READ_PATHS` 同样遗漏现有 `/api/news/wordcloud`，筛掉静态
路由后由文章 UUID 路由处理并产生 422。owner Caddy 已允许该现有
新闻路径。已授权仅补齐这一 owner 只读路由及相关读取／认证失败回归。
owner `/api/lhb` 与 public 一样不在 Caddy allowlist，默认拒绝为空正文
404；仅核对实际归档证券／日期／页面阶段下该精确 GET，不新增路由或
泛化允许 404／422。

owner 词云失败回归保留为
`contract-c-c1-owner-wordcloud-red.log`：owner 实际 422，1 failed／5 passed。
生产修复仅在 owner `_RESET_READ_PATHS` 增加现有词云路径；
同一 `tests/test_contract_c1_public_sentiment.py` 参数化 public／owner，
最终 `contract-c-c1-owner-wordcloud-green.log` 为 6 passed／零跳过，
核对合法归档 200、PUB／日期／原始归档不变、未知 owner PUB 精确 404，
以及未认证／无效会话 401。扩大的无数据库回归
`contract-c-c1-pure-backend-owner-green.log` 为 171 passed／24 deselected。
第十一次演练为 `27856-1795066867253-3fe25fa9`，最终 failed，
主错误为 `Unexpected end of JSON input`；manifest 记录清理 removed，
容器／卷／网络残留均为空，秘密文件已移除。定位为 paired 请求路径
先出现、JSON 写入尚未完成，控制器随即解析而失败；后续文件完整为
`{}`。owner 执行退出 137 是清理所致，不是主错误。夹具在两个检查点
方向改为校验 JSON、独占创建同目录临时文件并 rename 后才暴露最终
文件；补充 TypeScript 声明。聚焦失败回归更正后为 63 passed／零跳过，
尚待第十二轮真实演练，不能据此关闭 C1。
控制器独立无数据库回归为 171 passed／24 deselected，
Ruff、compileall 和 PRD 57／29／29／30 均通过。
2026-11-19 05:44 UTC 再次核对日常七服务／HEAD／
保护文件／CA／owner 身份，均未变化。
第十二轮真实演练为 `9496-1795067219267-fcffcdf1`，最终 passed，
命令退出 0；manifest 清理 removed、容器／卷／网络残留为空、
secretsRemoved 为 true。控制器独立读取实际证据：公开 54／owner 143
个 JS/CSS 资源通过；P1 与 P2 的实际归档目标可见，两类撤权均清空内容；
网络拒绝按实际路径／阶段／错误正文精确分类，非预期页面错误为空。
实现者已交付 DONE、冻结代码并释放容器执行槽位。
本次完整 C1 关闭仍待独立规格／质量复审与控制器两个最终容器门禁。
控制器前端完整回归日志 `contract-c-controller-frontend-final.log`
实际为 47 文件／1412 Vitest／96 Node，零失败与零跳过；
TypeScript 检查退出 0。执行前后 530 个源码文件哈希一致，
身份清单为 `contract-c-controller-frontend-source.json`；
591 个主工作区冻结文件也全部保持原始字节。
2026-11-19 05:25 UTC 在第八次清理后核对日常七服务／HEAD／
保护文件／CA／owner 身份未变；591 个主工作区冻结文件字节也未变。

## 保留的边界

本次没有授权提交、推送、远程 CI、更新日常实例、变更版本或发布部署。
两个 C1 容器构建不替代 C4 五组合构建；C1 浏览器结果不替代 C2 全视口、
真实缩放与全部返回分支。合成输入不替代真实来源许可或生产模型评估。
