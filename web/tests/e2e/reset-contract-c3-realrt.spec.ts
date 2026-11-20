import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';
import { parseRealtimeJson } from '../../src/features/realtime/json';

test('C3 actual container gateway renders pinned realtime evidence without mocks', async ({ page, browser }) => {
  const channel = process.env.C3_CHANNEL!;
  const inputs = JSON.parse(readFileSync(resolve(channel, 'inputs.json'), 'utf8'));
  async function checkpoint(name: string) {
    writeFileSync(resolve(channel, `${name}.request.json`), '{}', { flag: 'wx' });
    await expect.poll(() => existsSync(resolve(channel, `${name}.response.json`))).toBe(true);
  }
  const responses: { url: string; status: number; body: unknown }[] = [];
  page.on('response', async response => {
    if (response.url().includes('/api/realtime/') || response.url().includes('/sector-flow/intraday')) {
      responses.push({ url: response.url(), status: response.status(), body: parseRealtimeJson(await response.text(), new URL(response.url()).pathname) });
    }
  });
  await page.goto(`/?view=overview&snapshot=${inputs.publication.snapshot_id}&security=${inputs.security}&sector=${inputs.sector}&date=${inputs.date}`);
  const panel = page.getByRole('region', { name: '实时证据账本' });
  await expect(panel).toBeVisible();
  await panel.getByLabel('实时来源 ID', { exact: true }).fill(inputs.source);
  await panel.getByLabel('报价批次 ID').fill(inputs.quote);
  await panel.getByLabel('分钟批次 ID').fill(inputs.minute);
  await panel.getByLabel('资金流批次 ID').fill(inputs.flow);
  await panel.getByLabel('实时 PIT').fill(inputs.pit);
  await panel.getByRole('button', { name: '应用实时选择' }).click();
  await expect(panel.getByRole('table', { name: '实时报价', exact: true })).toContainText('123.45');
  const minute = panel.getByRole('table', { name: '分钟记录', exact: true });
  await expect(minute.locator('tbody tr')).toHaveCount(3);
  await expect(minute).toContainText('c3-minute');
  await expect(panel).toContainText('stream_gap');
  const flow = panel.getByRole('table', { name: '题材分时资金流审计记录' });
  await expect(flow).toContainText('synthetic-unavailable');
  await expect(flow).toContainText(inputs.flow);
  await expect(flow).toContainText(inputs.source);
  await expect(flow).toContainText('CNY');
  const anomalies = panel.getByRole('table', { name: '异动证据', exact: true });
  await expect(anomalies.locator('tbody tr')).toHaveCount(50);
  await checkpoint('late');
  await panel.getByRole('button', { name: '加载更多异动' }).click();
  await expect(anomalies.locator('tbody tr')).toHaveCount(100);
  await panel.getByRole('button', { name: '加载更多异动' }).click();
  await expect(anomalies.locator('tbody tr')).toHaveCount(111);
  await expect(anomalies).toContainText('relative_volume');
  await expect(anomalies).toContainText('window_return');
  await expect(anomalies.getByRole('row').filter({ hasText: 'c3-volume-110' }).locator('td[data-label="值"]')).toHaveText('1');
  await expect(anomalies.getByRole('row').filter({ hasText: 'c3-return-000' }).locator('td[data-label="值"]')).toHaveText('0.03');
  expect(inputs.duplicate_anomaly_replay).toEqual({
    status: 'duplicate', same_payload_hash: true, unique_events_after_replay: 111,
  });
  await expect(panel.getByLabel('异动分页统计')).toContainText('已累计加载 111 条唯一事件');
  await expect(panel.getByLabel('异动分页统计')).toContainText('不代表累计账本覆盖');
  await expect(panel.getByRole('button', { name: '加载更多异动' })).toHaveCount(0);
  const rules = await anomalies.locator('tbody th').allTextContents();
  expect(new Set(rules).size).toBe(111);
  const anomalyEvents = responses.filter(item => item.status === 200 && item.url.includes('/anomalies')
    && new URL(item.url).searchParams.get('source_id') === inputs.source
    && new URL(item.url).searchParams.get('as_of') === inputs.pit
    && new URL(item.url).searchParams.get('security_id') === inputs.security
    && new URL(item.url).searchParams.get('date') === inputs.date)
    .flatMap(item => (item.body as { data?: { events: { event_id: string; rule_version: string; value: string | null }[] } }).data?.events ?? []);
  for (const [rule, value] of [['c3-volume-110', '1'], ['c3-return-000', '0.03']]) {
    const matching = anomalyEvents.filter(event => event.rule_version === rule);
    expect(matching).toHaveLength(1);
    expect(matching[0].value).toBe(value);
  }
  const eventIds = anomalyEvents.map(event => event.event_id);
  expect([...new Set(eventIds)].sort()).toEqual([...inputs.event_ids].sort());
  await expect(anomalies).not.toContainText('c3-late');
  // A committed correction must not fall forward into an old PIT or exact old batch.
  await panel.getByLabel('实时 PIT').fill(inputs.old_pit);
  await panel.getByLabel('分钟批次 ID').fill('');
  await panel.getByRole('button', { name: '应用实时选择' }).click();
  await expect(minute).not.toContainText('c3-correction');
  await expect(minute).toContainText('修订 2');
  await panel.getByLabel('实时 PIT').fill(inputs.pit);
  await panel.getByLabel('分钟批次 ID').fill('c3-correction');
  await panel.getByRole('button', { name: '应用实时选择' }).click();
  await expect(minute).toContainText('105');
  await expect(minute).toContainText('修订 3');
  await panel.getByLabel('分钟批次 ID').fill(inputs.minute);
  await panel.getByRole('button', { name: '应用实时选择' }).click();
  await expect(minute).toContainText('修订 1');
  await expect(minute).not.toContainText('105');
  for (const gate of inputs.gates) {
    await panel.getByLabel('分钟批次 ID').fill(gate.batch);
    await panel.getByLabel('实时 PIT').fill(gate.before);
    await panel.getByRole('button', { name: '应用实时选择' }).click();
    await expect(minute).toHaveCount(0);
    for (const pit of [gate.boundary, gate.after]) {
      await panel.getByLabel('实时 PIT').fill(pit);
      await panel.getByRole('button', { name: '应用实时选择' }).click();
      await expect(minute).toContainText(gate.batch);
      await expect(minute).toContainText('修订 4');
    }
  }
  await panel.getByLabel('报价批次 ID').fill('missing-c3');
  await panel.getByRole('button', { name: '应用实时选择' }).click();
  await expect(panel.getByRole('table', { name: '实时报价', exact: true })).toHaveCount(0);
  await expect.poll(() => responses.some(item => item.status === 404
    && item.url.includes('quote_batch_id=missing-c3'))).toBe(true);
  expect(responses.every(item => !new URL(item.url).searchParams.has('snapshot_id'))).toBe(true);
  expect(responses.some(item => item.url.includes('/sector-flow')
    && new URL(item.url).searchParams.get('batch_id') === inputs.flow
    && new URL(item.url).searchParams.get('as_of') === inputs.pit
    && item.status === 200)).toBe(true);
  await checkpoint('revoke');
  await panel.getByRole('button', { name: '刷新实时证据' }).click();
  await expect(panel).toContainText('实时来源授权已撤销');
  await expect(panel.getByRole('table')).toHaveCount(0);
  writeFileSync(resolve(channel, 'evidence.json'), JSON.stringify({
    synthetic: true, actualContainerGateway: true, chromium: browser.version(),
    noRouteMocks: true, uniqueAnomalies: 111, responses,
  }));
});
