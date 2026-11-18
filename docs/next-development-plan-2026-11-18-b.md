# 批次 B：契约与需求状态收口实施计划

> **For agentic workers:** 使用 `subagent-driven-development`（推荐）或 `executing-plans` 逐任务实施。先冻结本计划的契约决定，再执行失败回归、最小修复、独立规格审查和质量审查。复用已交付交互；本文件是计划，本轮不实施源码。

**Goal:** 修复显式未知发布的选择边界，使 57 项 REQ、16 项 EX、现行契约和后续验收责任可逐项追溯。

**Architecture:** 保留不可变发布、发布时物化和读取不重算。公开、LOCAL、所有者任务、实时批次与龙虎榜分别使用各自身份和授权边界；状态登记不代替完整需求验收。

**Tech Stack:** FastAPI、PostgreSQL、React、TypeScript、pytest、Playwright、uv、Docker Compose。

---

## 当前基线与范围

- 主工作区 `main`：`ee4fd82`；实际运行源码：`ed7553a`。后续提交只记录运行结果，运行源码未变。
- 批次 A1–A3 已交付；七个服务正常。实际 Chrome 验证了公开空状态搜索反馈和受信 HTTPS 配对指引，见 [交付与运行记录](local-onboarding-2026-11-18.md)。
- 原有 `docs/README.md`、`docs/next-development-plan-2026-11-17.md` 修改保留；实施不得纳入本批覆盖或提交。
- 规划过程中还检测到其他源码、测试与契约的并发修改。本计划只新增本文件；
  下次实施须重新读取实际 HEAD、全部 dirty 清单和文件哈希，不能仅以 `ee4fd82`
  或最初两项文档修改代表完整工作区。已有并发修改均作为保留基线。
- [总计划](next-development-plan-2026-11-18.md) 的 B 为下一批。C 负责完整工程验收，D 负责真实输入、正式规则、真实模型和生产条件。
- B 的交付是选择边界修复、契约校准和状态登记；不增加行情字段、算法、公开私人任务接口或数据库迁移。

本轮已进行源码核对与两个公开 GET 的只读探针，没有运行测试或修改运行实例：

| 核对项 | 当前证据 | 本批建议 |
| --- | --- | --- |
| 显式未知发布 | `PublicationRepository.get_published_snapshot()` 可返回 `None`；`select_snapshot()` 原样返回。实际 `/api/market/overview`、`/api/stock/UNKNOWN` 携带同一未知 UUID 返回 200、`snapshot_id:null`、`no_data` | 统一显式未知 ID 为 404；保留按日期找不到发布的 200 无数据 |
| 测试替身差异 | `EmptyRepository.get_snapshot()` 抛 `NotFound`，既有 `test_explicit_nonexistent_snapshot_is_not_no_data` 因而要求 404 | 补充返回 `None` 的仓储替身，防止假一致 |
| 排名排序 | 后端 `_SORT_FIELDS` 与前端 `analysisSortDescriptor()` 固定方向；后续契约概括为双向排序 | 精确描述现行方向；把排名双向交互差距登记为独立功能规格，不用文案消除需求差距 |
| EX 通用 envelope | 早期段落概括所有 GET 使用每日发布字段；实时与龙虎榜有专用序列化 | 按身份族说明适用范围，不改变已冻结专用响应 |
| 需求状态 | 旧核对表仍有实施前的 AM01–AM07、交互差距与历史统计 | 以当前实现、具体断言和实际运行证据更新；未测子条款继续保留 |

## 契约决定

### 发布选择：建议冻结为以下规则

| 输入／条件 | HTTP 与行为 |
| --- | --- |
| 参数格式错误、重复或未知参数 | 400 `INVALID_PARAM`；访问仓储前拒绝 |
| 仅按日期选择，未找到可读发布 | 200 无数据 envelope；保留请求日期，未知覆盖为 null |
| 显式 ID 不存在，包括仓储返回 `None` | 404 `NOT_FOUND`；不改选最新，不返回有用数据 |
| ID 已存在但未授权、撤权或未发布 | 403；不泄露受保护内容，不映射成未知 ID |
| 授权通过后，显式日期与发布日期不一致 | 400 `INVALID_PARAM` |
| UI URL 日期与已授权摘要不一致 | 按现有 `App.tsx` 将 URL 日期规范化为该摘要日期；不据此改变 HTTP 参数错误规则 |
| 所选发布内实体不存在 | 404；与发布整体缺失分别记录 |

统一 404 是本计划提出的契约变更，不是当前运行行为。推荐在 HTTP 选择适配器处理
`None`，保留仓储的可空返回约定和既有授权异常。维持 200 也是可选兼容方案，但会
要求重新定义“显式坏 ID”和“按日期无发布”的区分；按路由保留不同结果会继续扩大歧义。
本计划采用统一 404 的推荐方案。

### 排名与专用身份

- `/api/rank/range` 现行 `return`、`excess_return`、`amount_change` 为降序，`drawdown` 为升序；同值按证券 ID 稳定排列，空值最后。API 当前不接受 `order`。
- 成员、角色候选／变动、题材、消息、搜索、展开行情分别登记分页和排序，不把一张表的通过结果迁移到另一张表。
- 排名双向交互若适用于 REQ-V03，继续标为 `interaction gap`，由后续排名交互专项负责；该专项先冻结参数兼容、默认方向与排名语义。C 的验证不能替代该功能修复。
- EX04–EX07 使用日内来源、批次、修订和 `as_of`；EX08–EX09 使用披露来源、版本、席位映射和披露时间。每日发布 ID 不替代这些身份。
- 公开 AI 表面保持确定性事实模板；所有者显式创建、读取和审核任务。任务成功、数字验证通过、人工审核通过是三个独立状态。

## 文件职责

| 文件 | 本批职责 |
| --- | --- |
| `src/dragon_radar/http/selection.py` | 唯一计划中的生产源码修复：显式 ID 的可空返回统一为 404 |
| `tests/test_reset_contracts.py` | 仓储返回 `None` 的失败回归及既有选择边界回归 |
| `tests/test_reset_full_scope.py` | 保留 57／16 数量与文件路径约定，补齐新交互的追溯入口 |
| `docs/contracts/workbench-api.md` | 选择真值表、分页适用范围、排序方向、GET 身份族与 AI 边界 |
| `docs/prd-interaction-audit-2026-11-17.md` | 逐项更新 57 REQ、16 EX、AM01–AM07；保留原始审计范围与历史结论 |
| `docs/prd-development-progress-2026-11-17.md` | 最新工程状态与验证限制，历史统计不作为当前通过证据 |
| 创建 `docs/contract-status-2026-11-18.md` | 本批决定、精确测试定位、实际命令结果、剩余子条款及负责批次 |

## Task B1：冻结 73 项状态基线

**读取：** 实际 PRD、核对表、workbench 契约、Stage1／IG04–IG11／批次 A 交付记录。

**修改：** 核对表；创建本批状态记录。此步不修改生产源码或测试。

- [ ] 核对 `scripts/check_prd_contracts.py` 的 `EXPECTED_REQS`，以及 `tests/test_reset_full_scope.py` 的 `REQ_REGISTRY`、`EX_REGISTRY`。
- [ ] 将 57 REQ 和 16 EX 逐项登记为：ID、适用子条款、当前行为、主状态、绑定身份、源码位置、测试定位、执行证据、剩余项、负责批次。
- [ ] 保留原有四个主状态：`implemented`、`interaction gap`、`production input missing`、`needs verification`。`implemented` 只说明明确范围的接线，不代表完整 REQ 或生产通过。
- [ ] 区分“测试文件存在”“断言覆盖该行为”“命令实际通过”；没有执行证据的断言登记为验证目标。
- [ ] 先记录 AM01–AM07 当前事实，B2–B5 完成后再更新结论；不批量把旧状态改成通过。

记录格式：

```text
ID | 适用子条款 | 当前行为 | 主状态 | 身份绑定 | 源码
   | 精确测试定位 | 实际执行证据 | 剩余子条款 | 负责批次
```

例如 REQ-G02 的分组搜索追溯到 `tests/test_reset_ig11_search.py::test_search_three_groups_share_one_pub_and_real_total`
及 `web/tests/e2e/reset-ig11.spec.ts` 的实际目标导航断言。它不自动证明命令索引完整、
所有历史消息齐备或所有视口通过。

**验收：** 恰好 57 个唯一 REQ 与 16 个唯一 EX；所有未覆盖子条款有明确负责范围。

## Task B2：修复显式未知发布的 404 边界

**修改：** `tests/test_reset_contracts.py`、`src/dragon_radar/http/selection.py`、workbench 契约。

- [ ] 在既有测试文件中增加以下回归，保留原有抛 `NotFound` 的替身用例：

```python
class MissingSelectionRepository(EmptyRepository):
    def get_snapshot(self, snapshot_id):
        self.calls.append(snapshot_id)
        return None


@pytest.mark.parametrize("path", ["/api/market/overview", "/api/stock/A"])
@pytest.mark.parametrize("requested_date", [None, "2026-09-10"])
async def test_explicit_unknown_snapshot_none_is_not_no_data(path, requested_date):
    repository = MissingSelectionRepository()
    snapshot_id = uuid4()
    params = {"snapshot_id": str(snapshot_id)}
    if requested_date is not None:
        params["date"] = requested_date
    async with workbench(repository) as client:
        response = await client.get(path, params=params)
    assert_error(response, 404, "NOT_FOUND", "Resource not found")
    assert repository.calls == [snapshot_id]


@pytest.mark.parametrize("path", ["/api/market/overview", "/api/stock/A"])
async def test_real_repository_unknown_publication_is_not_no_data(
    published_market_client, path
):
    client, snapshot = published_market_client
    response = await client.get(
        path,
        params={
            "date": snapshot.trade_date.isoformat(),
            "snapshot_id": str(uuid4()),
        },
    )
    assert_error(response, 404, "NOT_FOUND", "Resource not found")
```

- [ ] 执行该用例。预期当前源码的 4 个参数实例因收到 200 而失败；记录实际结果，禁止制造额外失败。

```powershell
uv run --no-sync pytest tests/test_reset_contracts.py::test_explicit_unknown_snapshot_none_is_not_no_data -q --basetemp E:/ByteDance/Projects/Code/.temp/contract-b-red -o cache_dir=E:/ByteDance/Projects/Code/.temp/contract-b-cache
```

- [ ] 将 `selection.py` 的导入改为：

```python
from dragon_radar.storage.repository import NotFound, Repository
```

将显式 ID 分支中的可空返回改为：

```python
snapshot = repository.get_snapshot(snapshot_id)
if snapshot is None:
    raise NotFound
```

日期比较、授权异常、无 ID 的列表选择和 `through_date` 分支保持现有规则。

- [ ] 复跑选择切片，预期无失败、无跳过：

```powershell
uv run --no-sync pytest tests/test_reset_contracts.py -q -k "explicit_unknown_snapshot_none or explicit_nonexistent_snapshot or absent_date or bad_selectors" --basetemp E:/ByteDance/Projects/Code/.temp/contract-b-green -o cache_dir=E:/ByteDance/Projects/Code/.temp/contract-b-cache
```

- [ ] 在独立 `dragon_test` 环境复测既有日期不匹配、真实撤权／到期与固定发布回归；不能仅用抛 `Forbidden` 的替身证明许可策略。
- [ ] 拆开原契约“missing date publication / unknown publication ID”一行，按上述真值表分别描述。记录显式未知 ID 从 200 到 404 的兼容性影响。

**验收：** 两种未知返回形式一致；无日期发布仍为 200；授权拒绝仍为 403；
日期错误仍为 400；不改选最新、不重算、不修改原始发布。

## Task B3：搜索、身份、分页与排名适用范围

**修改：** workbench 契约、核对表和本批状态记录；不扩展排名 API 参数。

**证据：** `tests/test_reset_ig11_search.py`、`tests/test_reset_analysis.py`、
`web/tests/unit/reset-ig11-search.test.ts`、`web/tests/unit/reset-analysis.test.ts`、
`web/tests/e2e/reset-ig11.spec.ts`、`web/tests/e2e/reset-analysis.spec.ts`。

- [ ] AM02 登记分组搜索的证券／题材／消息、合并分页、各组未知与真实零匹配、撤权后清空及实际目标导航。
- [ ] 写明题材详情使用真实 `sector_id`；不得从名称或当前标签推断 `topic_id`、历史成员或关联映射。
- [ ] AM04 为成员、角色候选、角色变动、题材成员、消息、排名、全局搜索、展开行情分别登记 20／50／100、默认 50、实际总数、排序字段、方向、空值与导出范围。
- [ ] 单列有限日期／板数矩阵、固定六组证据、指标摘要、小型关联预览的例外；消息保留发布时间排序。
- [ ] 将排名现行四种固定方向写入契约，链接 `_SORT_FIELDS`、`analysisSortDescriptor()` 和具体回归。通用“双向”表述不能继续涵盖该接口。
- [ ] 将排名双向需求差距保留在需求行，负责范围明确为后续排名交互专项；不把它登记成 C 尚未执行的纯验证项。

**验收：** 契约描述与当前 API／控件一致；表格例外有范围；需求未满足部分仍清晰可见。

## Task B4：EX 身份族与 AI 表面

**修改：** workbench 契约、核对表和本批状态记录。

**读取：** `src/dragon_radar/http/routes/realtime.py`、`src/dragon_radar/http/routes/lhb.py`、
`web/src/features/realtime/types.ts`、`web/src/features/lhb/types.ts`、
`web/src/features/ai/TaskPanel.tsx`。

- [ ] AM03 将 GET 分类为：PUB（EX10／EX11／EX16）、日内（EX04–EX07）、披露（EX08／EX09）、所有者任务结果（EX02／EX15）。保留 29 项原接口与 16 项 EX 的编号。
- [ ] 实时 quote 明确 `source_id + quote_batch_id`；分钟明确 `source_id + batch_id`、修订、交易日和 `as_of`。显式批次不替代，晚到数据不跨越可用时间。
- [ ] 龙虎榜明确 `date/as_of/source/source_version`、席位来源与 `mapping_version`、披露修订；当前响应具有专用 coverage/basis，不补造每日 `snapshot_id` 或 `rule_version`。
- [ ] 将早期 GET 总述替换为如下适用规则，再分别链接专用序列化与客户端校验：

```text
GET extensions follow their domain's frozen identity and envelope.
Publication-bound reads use the selected immutable daily publication.
Intraday reads use source, batch/revision and point-in-time availability.
Disclosure reads use disclosed-source and mapping/revision provenance.
Owner task results use initiating-principal and immutable task/input identity.
The common daily envelope does not override a specialized domain contract.
```

- [ ] AM05 记录公开确定性模板与所有者显式任务／审核的边界；不能把 `succeeded` 当作已验证且已人工审核。
- [ ] 单列指数价格历史归档缺项和公开已审核每日 AI 的发现机制缺项。新增归档字段或公开发现接口属于独立产品规格，不从当前行情或私人任务兜底。

**验收：** 通用说明不再与专用协议矛盾；所有者任务没有被公开发现或读取；原有包体与网关隔离不变。

## Task B5：追溯注册表、实际回归与 C 交接

**修改：** 全范围测试的追溯元数据、开发进度、本批状态记录和核对表。

- [ ] 在 `REQ_REGISTRY` 原条目内补充证据：G01 增加 `tests/test_reset_contracts.py`；G02 增加 `tests/test_reset_ig11_search.py`、`web/tests/unit/reset-ig11-search.test.ts`、`web/tests/e2e/reset-ig11.spec.ts`。保留已有证据。
- [ ] 注册表的 `evidence` 仍为实际文件路径 tuple，不能塞入 `tests/test_reset_contracts.py::test_explicit_nonexistent_snapshot_is_not_no_data`；精确测试定位写入状态记录。每条新路径不得重复。
- [ ] 按以下字典值编辑原 G01／G02 条目，不在文件末尾另加重复赋值。其他 55 个 REQ 条目保留；这是追溯元数据更新，不是整体需求通过声明：

```python
REQ_REGISTRY["G01"] = {
    "tasks": "02、03、08",
    "evidence": (
        "tests/test_reset_publication.py",
        "tests/test_reset_market.py",
        "web/tests/e2e/reset-market.spec.ts",
        "web/src/pages/Overview.tsx",
        "tests/test_reset_contracts.py",
    ),
}
REQ_REGISTRY["G02"] = {
    "tasks": "09、14、IG11",
    "evidence": (
        "tests/test_reset_market.py",
        "tests/test_reset_news.py",
        "web/tests/e2e/reset-navigation.spec.ts",
        "tests/test_reset_ig11_search.py",
        "web/tests/unit/reset-ig11-search.test.ts",
        "web/tests/e2e/reset-ig11.spec.ts",
    ),
}
```

- [ ] 执行下面的完整后端切片。前置条件是保护配置中的 `DRAGON_TEST_DATABASE_URL` 指向可丢弃 `dragon_test`；`tests/conftest.py` 创建并清理独立 schema。

```powershell
if (-not $env:DRAGON_TEST_DATABASE_URL) { throw 'Provide the protected disposable dragon_test connection before this gate.' }
uv run --no-sync pytest tests/test_reset_contracts.py tests/test_reset_public_gateway.py tests/test_reset_full_scope.py tests/test_reset_ig11_search.py tests/test_search_legacy_archives.py tests/test_reset_analysis.py tests/test_reset_realtime.py tests/test_reset_lhb.py -q --basetemp E:/ByteDance/Projects/Code/.temp/contract-b-pytest -o cache_dir=E:/ByteDance/Projects/Code/.temp/contract-b-cache
```

缺少测试库时停止该门禁；不把自动跳过记为成功。Windows／Linux 结构性不可运行的探针
按既有收集／精确 deselection 规则记录，不放宽授权或性能阈值。

- [ ] 执行源码与前端回归，记录真实命令、退出码、源码身份与结果；完整 Vitest／Node 只在本批稳定后集中运行一次：

```powershell
uv run --no-sync python scripts/check_prd_contracts.py
uv run --no-sync ruff check src tests
uv run --no-sync python -m compileall -q src
& .\web\node_modules\.bin\tsc.cmd --noEmit --project .\web\tsconfig.json
npm --prefix web test
$env:PLAYWRIGHT_CHANNEL = 'chrome'
npm --prefix web run test:e2e:reset -- reset-ig11.spec.ts reset-analysis.spec.ts --output E:/ByteDance/Projects/Code/.temp/contract-b-browser
```

预期 PRD checker 报告 `REQ=57 endpoints=29`，退出 0；例子与 JSON 数量采用
命令的实际输出，57／29 是不得变化的契约数量。
其余门禁要求零错误和对应切片通过；不能用计划中的预期数量代替命令结果。

- [ ] AM06 保留历史记录，最新进度只引用本批及适用交付记录的实际证据；不重建已删除日志，也不迁移历史测试总数为当前通过结论。
- [ ] AM07 将以下项目交给 C，每项列明确测试对象、构建变体、源码／产物身份和验收结果：

| C 的检查项 | 验收要求 |
| --- | --- |
| 视口、200% 与对比度 | 按 PRD 3.3／AC-V06 四档执行；明确 375px 手机与 1440px 桌面主路径，记录具体宽高和缩放方式 |
| 关闭、返回与滚动恢复 | 实际目标可见，返回原上下文与滚动位置，Esc 后隐藏并恢复触发器焦点 |
| AI 60s／120s | 受控时钟分别验证处理中提示、确定性模板与迟到草稿不覆盖保存文本；实际延迟证据另列 |
| 实时行为与出站边界 | RT01–RT12、批次／修订／授权／可用时间及 provider egress；不以每日发布绑定替代 |
| 实际 Caddy → API | 在隔离合成实例验证有效发布的题材、搜索、关联复核与引用定位，以及公开写入／私人路径／owner 会话／撤权 |
| 构建与性能 | 五变体预算、懒加载和物理隔离；专用性能门禁 P95 ≤2.0s，先于浏览器负载，CI runner 为 `ubuntu-24.04` |
| 完整验收与 CI | 隔离 Linux 完整 verifier 保留 90 分钟预算；远程 CI、提交、运行实例更新按后续实际授权执行 |

**验收：** 57／16 注册表、需求核对表与实际断言一致；AM01–AM07 都有明确决定、
当前状态或负责范围。排名功能差距不混入纯验证清单，真实条件仍归 D。

## 执行与关闭边界

- [ ] 实施先重新确认 HEAD 与当前工作区，再建项目外隔离工作树；冻结实际提交、
  全部原有／并发修改的清单和哈希。重新核对选择、排序与专用身份源码后应用本计划，
  不把历史 `ee4fd82` 的干净文件覆盖到当前工作区。中间文件、缓存和日志统一在项目外 `.temp`。
- [ ] 按 B1 → B2 → B3 → B4 → B5 顺序执行；同一契约和核对表不得由多个实现代理并行写入。每个任务完成后依次做规格审查、质量审查。
- [ ] 将已证实问题集中修复，再集中回归；已通过门禁只在新修改、失败或未解疑点出现时补跑。
- [ ] 对照清单和冻结哈希回传主工作区，保留原有两项文档及全部并发修改；
  同一路径变更先核对差异，不直接覆盖。源码、测试与文档结论必须一致。
- [ ] 本计划交付不自动授权提交、推送、更新运行实例、改变版本、正式发布或部署；这些按之后的实际指令执行。

**B 完成条件：** 显式未知 ID 的 `None` 路径有 RED → GREEN 证据；日期／许可边界不退化；
73 项追溯与 AM01–AM07 校准；剩余功能差距、C 工程验证和 D 外部条件分别列明。
不据此宣布全部 PRD、完整 Linux 门禁或生产条件通过。
