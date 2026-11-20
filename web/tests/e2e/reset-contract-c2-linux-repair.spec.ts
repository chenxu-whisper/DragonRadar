import { expect, test } from '@playwright/test';
import { writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { ownedBrowser, hitTest } from '../fixtures/contract-c2-browser';
import { mockC2, ID, DATE } from '../fixtures/contract-c2-scenes';

for (const kind of ['regulation-grid', 'stock-sort'] as const) {
  test(`C2 Linux repair ${kind} real controls and container bounds`, async () => {
    const owned = await ownedBrowser(kind === 'stock-sort'
      ? { width: 1440, height: 1000 } : { width: 768, height: 1024 }, 'light');
    const proof: Record<string, unknown> = { kind };
    let failure;
    try {
      const audit = await mockC2(owned.page);
      const path = kind === 'stock-sort' ? '/stock/TEST001?view=stock' : '/?view=regulation';
      await owned.page.goto(`http://127.0.0.1:4172${path}&snapshot=${ID}&date=${DATE}`);
      proof.receipt = await owned.zoom(kind === 'stock-sort' ? 2 : 1);
      if (kind === 'stock-sort') {
        await owned.page.getByText(/查看完整行情数据/).click();
        const table = owned.page.getByRole('table', { name: '完整行情数据', exact: true });
        const sort = table.getByRole('button', { name: '交易日期排序', exact: true });
        proof.sortBounds = await sort.evaluate(element => {
          const head = element.closest('thead')!;
          return { control: element.getBoundingClientRect().toJSON(), header: head.getBoundingClientRect().toJSON(),
            clip: getComputedStyle(head).clip, position: getComputedStyle(head).position };
        });
        proof.hit = await hitTest(sort);
        await sort.click();
        await expect(table.locator('tbody tr').first()).toContainText(DATE);
      } else {
        await expect(owned.page.locator('.formal-event-list article')).toHaveCount(4);
        proof.bounds = await owned.page.locator('.formal-event-list article').first().evaluate(element => ({
          rect: element.getBoundingClientRect().toJSON(), columns: getComputedStyle(element).gridTemplateColumns,
          children: [...element.children].map(child => child.getBoundingClientRect().toJSON()),
          viewport: document.documentElement.clientWidth, rootScroll: document.documentElement.scrollWidth,
        }));
        expect((proof.bounds as { rootScroll: number; viewport: number }).rootScroll)
          .toBeLessThanOrEqual((proof.bounds as { viewport: number }).viewport + 1);
      }
      await owned.page.waitForLoadState('networkidle');
      await audit.settled();
      audit.assertClean();
    } catch (error) { failure = error; proof.error = String(error); }
    finally {
      try { proof.cleanup = await owned.cleanup(); }
      catch (error) { failure = new AggregateError([failure, error].filter(Boolean), 'Repair cleanup failed'); }
      proof.outcome = failure ? 'failed' : 'passed';
      writeFileSync(resolve(process.env.C2_EVIDENCE_DIR || '/evidence', `repair-${kind}.json`), JSON.stringify(proof, null, 2));
    }
    if (failure) throw failure;
  });
}
