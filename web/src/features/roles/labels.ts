import type { RoleStatus } from './types';

const UNKNOWN = '未识别状态';

const ROLE_LABELS: Record<string, string> = {
  emotion: '情绪龙头',
  emotion_leader: '情绪龙头',
  trend: '趋势龙头',
  trend_leader: '趋势龙头',
  core: '中军',
  zhongjun: '中军',
  follower: '跟风',
  genfeng: '跟风',
  catchup: '补涨',
  buZhang: '补涨',
  relay: '接力龙头',
  jieli: '接力龙头',
};

const EVIDENCE_STATUS_LABELS: Record<string, string> = {
  candidate: '正式候选',
  insufficient: '证据不足',
  waiting_verify: '等待验证',
  watch: '仅观察',
  computed: '已计算',
  unavailable: '暂不可用',
};

const CAPABILITY_LABELS: Record<string, string> = {
  ready: '正式候选能力',
  simplified: '简化规则',
  watch_only: '仅观察',
  pending_validation: '待验证',
};

const STATUS_LABELS: Record<string, string> = {
  ready: '可正式候选',
  simplified: '简化规则',
  watch_only: '仅观察',
  pending_validation: '待验证',
  insufficient: '证据不足',
  no_candidate: '暂无候选',
  unavailable: '暂不可用',
  loading: '加载中',
};

const CHANGE_TYPE_LABELS: Record<string, string> = {
  added: '新增',
  exited: '退出',
  tie_changed: '并列关系变化',
  unchanged: '未变化',
};

const COMPARISON_STATUS_LABELS: Record<string, string> = {
  comparable: '可比较',
  not_comparable: '不可比较',
};

const REASON_LABELS: Record<string, string> = {
  promotion_observation_missing: '晋级观察数据缺失',
  rule_version_changed: '规则版本已变化',
  topic_membership_changed: '题材成员已变化',
  entered_rank: '进入排名',
  rule_version_not_approved: '规则版本尚未批准',
  historical_research_only: '仅用于历史研究',
  required_inputs_missing: '必要输入缺失',
  required_input_unavailable: '必要输入不可用',
  no_ranked_candidate: '没有满足排名条件的候选',
  capability_not_formal_candidate: '当前能力不支持正式候选',
  pending_validation: '等待验证',
  role_input_insufficient: '角色输入不足',
  previous_comparable_run_unavailable: '缺少可比较的前次运行',
  predecessor_run_unavailable: '前序运行不可用',
  topic_unavailable: '题材数据不可用',
  topic_incomparable: '题材不可比较',
  market_benchmark_unavailable: '市场基准不可用',
  benchmark_identity_unavailable: '基准身份不可用',
  security_identity_unavailable: '证券身份不可用',
  topic_mdd_median_unavailable: '题材回撤中位数不可用',
  topic_batch_unavailable: '题材批次不可用',
  topic_source_grant_unavailable: '题材来源授权不可用',
  topic_member_identity_unavailable: '题材成员身份不可用',
  factor_arithmetic_unavailable: '因子计算不可用',
  amount_window_incomplete: '成交额窗口不完整',
  turnover_rate_unavailable: '换手率不可用',
  float_mv_unavailable: '流通市值不可用',
  unique_trend_reference_unavailable: '唯一趋势参照不可用',
  trend_reference_return5_unavailable: '趋势参照五日收益不可用',
  unique_trend_core_unavailable: '唯一趋势核心不可用',
  relay_factor_input_unavailable: '接力因子输入不可用',
  first_limit_time_unavailable: '首次涨停时间不可用',
  open_reseal_evidence_unavailable: '开板回封证据不可用',
  seal_order_quality_unavailable: '封单质量不可用',
  intraday_start_order_unavailable: '盘中启动顺序不可用',
  independent_catalyst_evidence_unavailable: '独立催化证据不可用',
  intraday_seal_detail_unavailable: '盘中封板明细不可用',
  intraday_seal_fields_unavailable: '盘中封板字段不可用',
  role_disabled: '角色域未发布',
  role_missing: '角色数据缺失',
  role_failed: '角色计算失败',
  role_pending: '角色计算未完成',
  role_partial: '部分可用',
  seal_event_missing: '无已发布封板事件',
  regulation_not_implemented: '监管数据不可用',
  regulation_disabled: '监管数据未发布',
  relative_strength_positive: '相对强度为正',
  lags_unique_core: '落后于唯一核心标的',
  first_observation: '首次观察',
  topic_revision_changed: '题材修订已变化',
  taxonomy_version_changed: '题材分类版本已变化',
  membership_version_changed: '题材成员版本已变化',
  member_pool_changed: '题材成员池已变化',
  previous_comparable_run_mismatch: '前驱可比运行不匹配',
  not_previous_comparable_session: '前驱不是相邻可比交易日',
  no_core_candidate: '无核心候选',
};

const ROTATION_CHANGE_LABELS: Record<string, string> = {
  segment_start: '新观察段',
  unchanged: '核心未变',
  expanded: '核心扩大',
  contracted: '核心缩小',
  reorganized: '核心重组',
};

const METRIC_LABELS: Record<string, string> = {
  observed_factor: '观察因子',
  relative_strength: '相对强度',
  relative_strength_20: '20日相对强度',
  turnover_stability_20: '20日换手稳定性',
  float_mv: '流通市值',
  period_return: '区间收益',
};

function lookup(labels: Record<string, string>, value: string | null | undefined): string {
  return value ? labels[value] ?? UNKNOWN : UNKNOWN;
}

export const roleLabel = (value: string | null | undefined) => lookup(ROLE_LABELS, value);
export const evidenceStatusLabel = (value: string | null | undefined) => lookup(EVIDENCE_STATUS_LABELS, value);
export const capabilityLabel = (value: string | null | undefined) => lookup(CAPABILITY_LABELS, value);
export const statusLabel = (value: string | null | undefined) => lookup(STATUS_LABELS, value);
export const changeTypeLabel = (value: string | null | undefined) => lookup(CHANGE_TYPE_LABELS, value);
export const comparisonStatusLabel = (value: string | null | undefined) => lookup(COMPARISON_STATUS_LABELS, value);
export const reasonLabel = (value: string | null | undefined) => lookup(REASON_LABELS, value);
export const reasonLabels = (values: string[]) => values.map(reasonLabel);
export const rotationChangeLabel = (value: string | null | undefined) => lookup(ROTATION_CHANGE_LABELS, value);
export const metricLabel = (value: string | null | undefined) => lookup(METRIC_LABELS, value);

const STATUS_PRIORITY: Record<RoleStatus['status'], number> = {
  insufficient: 0,
  pending_validation: 1,
  watch_only: 1,
  no_candidate: 2,
  simplified: 3,
  ready: 4,
};

export function aggregateRoleStatuses(statuses: RoleStatus[]): Pick<RoleStatus, 'status' | 'reasons'> | undefined {
  if (!statuses.length) return undefined;
  const weakest = statuses.reduce((current, item) =>
    STATUS_PRIORITY[item.status] < STATUS_PRIORITY[current.status] ? item : current);
  return {
    status: weakest.status,
    reasons: [...new Set(statuses.flatMap(item => item.reasons))],
  };
}
