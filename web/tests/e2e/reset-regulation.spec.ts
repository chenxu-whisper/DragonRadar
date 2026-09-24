import { expect, test } from '@playwright/test';

import {
  REGULATION_DATE,
  REGULATION_SNAPSHOT,
  mockRegulation,
} from '../fixtures/regulation';

const regulationUrl =
  `/?view=regulation&snapshot=${REGULATION_SNAPSHOT}&date=${REGULATION_DATE}`;

test('renders completeness, calculations and formal events as independent regions', async ({ page }) => {
  await mockRegulation(page);
  await page.goto(regulationUrl);

  await expect(page.getByRole('heading', { name: '监管', exact: true })).toBeVisible();
  const completeness = page.getByRole('region', { name: '数据完整性' });
  const calculations = page.getByRole('region', { name: '数值计算状态' });
  const events = page.getByRole('region', { name: '已披露正式事件与限制' });

  await expect(completeness).toContainText('部分完整');
  await expect(completeness.getByText('预期', { exact: true })).toBeVisible();
  await expect(completeness.getByText('有效', { exact: true })).toBeVisible();
  await expect(completeness.getByText('缺失', { exact: true })).toBeVisible();
  await expect(completeness.locator('dd')).toHaveText(['4', '3', '0', '1']);
  await expect(completeness).toContainText('来源校验通过');

  await expect(calculations.getByText('待核验', { exact: true })).toBeVisible();
  await expect(calculations.getByText('未触发', { exact: true })).toBeVisible();
  await expect(calculations.getByText('接近预警', { exact: true })).toBeVisible();
  await expect(calculations.getByText('计算触发', { exact: true })).toBeVisible();
  const pending = calculations.getByRole('row', { name: /synthetic-pending/ });
  await expect(pending).toContainText('0.40');
  await expect(pending).toContainText('待评审');
  await expect(pending).toContainText('待核验');
  await expect(pending).toContainText('规则阈值 0.70');
  await expect(pending).toContainText('提醒阈值 0.65');
  await expect(pending.getByRole('link', { name: '规则原文' })).toHaveAttribute(
    'href',
    /exchange\.example\.test/,
  );

  await expect(events).toContainText('4 条正式记录');
  await expect(events.getByText('已披露', { exact: true })).toHaveCount(2);
  await expect(events.getByText('公开监控', { exact: true })).toBeVisible();
  await expect(events.getByText('实际限制', { exact: true })).toBeVisible();
  await expect(events.getByRole('link', { name: '公告原文' })).toHaveCount(4);
  await expect(events).not.toContainText('synthetic-triggered');

  await expect(page.getByText('合成规则仅用于测试，不代表生产监管规则支持。')).toBeVisible();
  await expect(page.getByText(/安全再涨几板|无风险/)).toHaveCount(0);
});

test('unknown event source is not rendered as zero formal events', async ({ page }) => {
  await mockRegulation(page, { unknownEvents: true });
  await page.goto(regulationUrl);

  const events = page.getByRole('region', { name: '已披露正式事件与限制' });
  await expect(events).toContainText('事件来源完整性未知');
  await expect(events).toContainText('4 条已列记录');
  await expect(events.getByRole('link', { name: '公告原文' })).toHaveCount(4);
  await expect(events).not.toContainText('0 条正式记录');

  await page.goto(
    `/?view=stock&snapshot=${REGULATION_SNAPSHOT}&date=${REGULATION_DATE}` +
    '&security=TEST001&return_view=regulation',
  );
  await expect(page.getByText('正式监管事件：4')).toBeVisible();
  await expect(page.getByText(/事件来源完整性：未知/)).toBeVisible();
});

test('formal events remain visible when regulation status is unavailable', async ({ page }) => {
  await mockRegulation(page, { statusUnavailable: true });
  await page.goto(regulationUrl);

  await expect(page.getByRole('alert')).toContainText('数据服务暂不可用');
  const events = page.getByRole('region', { name: '已披露正式事件与限制' });
  await expect(events).toContainText('4 条正式记录');
  await expect(events.getByRole('link', { name: '公告原文' })).toHaveCount(4);
  await expect(page.getByRole('region', { name: '数据完整性' })).toHaveCount(0);
  await expect(page.getByRole('region', { name: '数值计算状态' })).toHaveCount(0);
});

test('validated status events are the fallback when the events request fails', async ({ page }) => {
  await mockRegulation(page, { eventsUnavailable: true });
  await page.goto(regulationUrl);

  await expect(page.getByRole('alert')).toContainText('数据服务暂不可用');
  const events = page.getByRole('region', { name: '已披露正式事件与限制' });
  await expect(events).toContainText('4 条已列记录');
  await expect(events.getByRole('link', { name: '公告原文' })).toHaveCount(4);
});

test('complete numerical inputs do not activate a pending rule', async ({ page }) => {
  await mockRegulation(page, { completeInputs: true });
  await page.goto(regulationUrl);

  const completeness = page.getByRole('region', { name: '数据完整性' });
  const calculations = page.getByRole('region', { name: '数值计算状态' });
  await expect(completeness).toContainText('完整');
  const pending = calculations.getByRole('row', { name: /synthetic-pending/ });
  await expect(pending).toContainText('待核验');
  await expect(pending).toContainText('待评审');
  await expect(pending).toContainText('未启用');
});

test('security detail reports formal event count and event-source completeness', async ({ page }) => {
  await mockRegulation(page);
  await page.goto(
    `/?view=stock&snapshot=${REGULATION_SNAPSHOT}&date=${REGULATION_DATE}` +
    '&security=TEST001&return_view=regulation',
  );

  await expect(page.getByRole('heading', { name: '合成测试证券1' })).toBeVisible();
  await expect(page.getByText('正式监管事件：4')).toBeVisible();
  await expect(page.getByText('事件来源完整性：完整')).toBeVisible();
  await expect(page.getByText(/安全再涨几板|无风险/)).toHaveCount(0);
});
