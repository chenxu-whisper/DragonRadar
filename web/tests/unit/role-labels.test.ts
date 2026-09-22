import { describe, expect, it } from 'vitest';
import {
  aggregateRoleStatuses,
  capabilityLabel,
  changeTypeLabel,
  comparisonStatusLabel,
  evidenceStatusLabel,
  reasonLabel,
  roleLabel,
  statusLabel,
} from '../../src/features/roles/labels';
import type { RoleStatus } from '../../src/features/roles/types';

describe('role presentation labels', () => {
  it('maps internal and external role codes to one Chinese vocabulary', () => {
    expect(roleLabel('trend')).toBe('趋势龙头');
    expect(roleLabel('trend_leader')).toBe('趋势龙头');
    expect(roleLabel('core')).toBe('中军');
    expect(roleLabel('zhongjun')).toBe('中军');
  });

  it('maps statuses, capabilities, changes and common reasons without exposing codes', () => {
    expect(evidenceStatusLabel('candidate')).toBe('正式候选');
    expect(capabilityLabel('pending_validation')).toBe('待验证');
    expect(statusLabel('no_candidate')).toBe('暂无候选');
    expect(changeTypeLabel('added')).toBe('新增');
    expect(comparisonStatusLabel('not_comparable')).toBe('不可比较');
    expect(reasonLabel('promotion_observation_missing')).toBe('晋级观察数据缺失');
    expect(reasonLabel('rule_version_changed')).toBe('规则版本已变化');
    expect(reasonLabel('topic_membership_changed')).toBe('题材成员已变化');
    expect(reasonLabel('entered_rank')).toBe('进入排名');
    expect(reasonLabel('rule_version_not_approved')).toBe('规则版本尚未批准');
    expect(reasonLabel('historical_research_only')).toBe('仅用于历史研究');
  });

  it('uses a conservative Chinese fallback for unknown codes', () => {
    expect(roleLabel('future_role')).toBe('未识别状态');
    expect(evidenceStatusLabel('future_status')).toBe('未识别状态');
    expect(reasonLabel('future_reason_code')).toBe('未识别状态');
  });
});

describe('all-topic role status aggregation', () => {
  it('selects the weakest topic and combines topic reasons instead of using the first row', () => {
    const statuses: RoleStatus[] = [
      { sector_id: 'SEC001', status: 'ready', capability: 'ready', reasons: ['entered_rank'] },
      { sector_id: 'SEC002', status: 'insufficient', capability: 'ready', reasons: ['required_inputs_missing'] },
    ];

    expect(aggregateRoleStatuses(statuses)).toEqual({
      status: 'insufficient',
      reasons: ['entered_rank', 'required_inputs_missing'],
    });
  });

  it('keeps waiting states weaker than no-candidate and ready states', () => {
    const statuses: RoleStatus[] = [
      { sector_id: 'SEC001', status: 'no_candidate', capability: 'ready', reasons: [] },
      { sector_id: 'SEC002', status: 'pending_validation', capability: 'pending_validation', reasons: [] },
      { sector_id: 'SEC003', status: 'ready', capability: 'ready', reasons: [] },
    ];

    expect(aggregateRoleStatuses(statuses)?.status).toBe('pending_validation');
  });
});
