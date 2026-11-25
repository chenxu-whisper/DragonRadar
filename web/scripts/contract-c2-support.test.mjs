import assert from 'node:assert/strict';
import test from 'node:test';
import { createHash } from 'node:crypto';
import { assertMatrix, cellKey, expectedCells } from './contract-c2-support.mjs';
import * as support from './contract-c2-support.mjs';

const legacy = () => expectedCells().map(cell => ({
  ...cell, measured: { ...cell.viewport, innerWidth: cell.viewport.width / cell.zoom,
    innerHeight: cell.viewport.height / cell.zoom },
  nativeGeometry: { innerWidth: cell.viewport.width, innerHeight: cell.viewport.height, outerWidth: 800, outerHeight: 1200 },
  changedGeometry: { innerWidth: Math.round(cell.viewport.width / cell.zoom), innerHeight: Math.round(cell.viewport.height / cell.zoom),
    outerWidth: 800, outerHeight: 1200 },
  receipt: { requested: cell.zoom, actual: cell.zoom, tabId: 7 },
  identity: cell.surface === 'review' ? 'LOCAL' : 'PUB/test/2026-09-10',
  operation: 'fixture target read and persisted/exported', outcome: 'passed',
  cleanup: { restored: 1, closed: true, profileRemoved: true },
}));
const pub = '00000000-0000-4000-8000-000000000001';
const hash = createHash('sha256').update('fixture').digest('hex');
function validDownload(cell) {
  if (cell.surface === 'review') return { filename: 'dragon-radar-review-2026-11-19.json', rowCount: 1, verified: true,
    decoded: JSON.stringify({ format_version: 1,
      notes: [{ note_id: 'note-1', current_revision: 1, snapshot_id: pub, trade_date: '2026-09-10' }],
      revisions: [{ note_id: 'note-1', revision: 1, content: { title: 'title', content: 'body' } }] }) };
  const headers = [...support.metadataColumns, ...support.dataColumns[cell.surface]];
  const targets = cell.surface === 'stock' ? Array(22).fill('TEST001') : support.targetSets[cell.surface];
  const rows = targets.map(target => {
    const row = Object.fromEntries(headers.map(h => [h, '']));
    Object.assign(row, { snapshot_id: pub, trade_date: '2026-09-10' });
    if (cell.surface === 'news') row.article_id = target;
    else if (cell.surface === 'regulation') [row.record_type, row.rule_id, row.security_id] = target.split('/');
    else row.security_id = target;
    return row;
  });
  const scope = { overview: 'overview-current-filter', news: 'news-current-page', leaders: 'leaders-current-filter',
    analysis: 'analysis-current-page', stock: 'stock-current-filter', regulation: 'regulation-current-filter' }[cell.surface];
  return { filename: `dragon-radar-${scope}-2026-09-10-00000000.csv`, rowCount: rows.length, verified: true,
    expectedRows: structuredClone(rows), decoded: [headers.join(','), ...rows.map(r => headers.map(h => r[h]).join(','))].join('\r\n') };
}
const complete = () => legacy().map(cell => ({
  ...cell,
  receipt: { ...cell.receipt, scope: 'chrome.tabs', appUrl: 'http://127.0.0.1:4172/', browser: '153.0.8010.12' },
  identity: cell.surface === 'review'
    ? { kind: 'LOCAL', recordId: 'note-1', revision: 1, snapshotId: pub, date: '2026-09-10' }
    : { kind: 'PUB', snapshotId: pub, date: '2026-09-10', targets: [...support.targetSets[cell.surface]] },
  download: validDownload(cell),
  readback: cell.surface === 'review' ? { recordId: 'note-1', revision: 1, title: 'title', body: 'body', reloaded: true } : undefined,
  reading: { contrast: 5, text: 'actual body', color: 'rgb(0,0,0)', background: 'rgb(255,255,255)',
    rootWidth: 375, rootScroll: 375, tables: [], scrollProof: [] },
  build: { variant: cell.variant, assetsSha256: hash },
})); 
test('applicability is exactly 320 unique cells, 160 independent pairs and no public review', () => {
  const cells = expectedCells();
  assert.equal(cells.length, 320);
  assert.equal(new Set(cells.map(cellKey)).size, 320);
  assert.equal(cells.filter(cell => cell.zoom === 1).length, 160);
  assert.equal(cells.filter(cell => cell.variant === 'public' && cell.surface === 'review').length, 0);
});
test('matrix accepts complete measured task, zoom and cleanup evidence', () => {
  assertMatrix(complete());
});
test('matrix accepts actual news CSV receipt and immutable revision columns', () => {
  const cells = complete();
  const cell = cells.find(value => value.surface === 'news');
  const headers = [...support.metadataColumns,
    'article_id', 'article_type', 'source', 'source_version', 'title',
    'published_at', 'received_at', 'available_at', 'revision', 'revision_id'];
  const rows = cell.download.expectedRows.map((row, index) => Object.fromEntries(
    headers.map(header => [header, header === 'received_at' ? '2026-09-10T01:00:00Z'
      : header === 'revision_id' ? `immutable-revision-${index}` : row[header] ?? '']),
  ));
  cell.download.expectedRows = rows;
  cell.download.decoded = [headers.join(','),
    ...rows.map(row => headers.map(header => row[header]).join(','))].join('\r\n');
  assertMatrix(cells);
});
test('C5 raw file binding rejects a changed recorded size', () => {
  assert.throws(() => support.assertBuildEvidence({
    source: [{ path: 'web/package-lock.json', sha256: hash, size: 99 },
      { path: 'web/src/App.tsx', sha256: hash, size: 7 }, { path: 'web/package.json', sha256: hash, size: 7 }],
    tools: { node: 'v24.14.0', playwright: '1.63.0', typescript: '5', vite: '8' },
    lockPackages: { 'node_modules/playwright': { version: '1.63.0' },
      'node_modules/typescript': { version: '5' }, 'node_modules/vite': { version: '8' } },
    readBytes: () => Buffer.from('fixture'), builds: [],
  }), /size/);
});
test('matrix rejects a missing cell', () => {
  assert.throws(() => assertMatrix(complete().slice(1)), /missing|count/);
});
test('matrix rejects duplicates even at the expected total count', () => {
  const cells = complete();
  cells[1] = cells[0];
  assert.throws(() => assertMatrix(cells), /duplicate/);
});
test('matrix rejects wrong applicability and measured viewport', () => {
  const cells = complete();
  cells[0].surface = 'review';
  assert.throws(() => assertMatrix(cells), /unexpected/);
  const wrong = complete();
  wrong[0].measured.width = 376;
  assert.throws(() => assertMatrix(wrong), /viewport/);
});
test('matrix rejects simulated zoom, failed operations and incomplete cleanup', () => {
  for (const mutate of [
    cell => { cell.receipt.actual = 1; },
    cell => { cell.outcome = 'failed'; },
    cell => { cell.cleanup.restored = 2; },
    cell => { cell.cleanup.closed = false; },
    cell => { cell.cleanup.profileRemoved = false; },
    cell => { cell.operation = ''; },
    cell => { cell.identity = ''; },
  ]) {
    const cells = complete();
    mutate(cells[1]);
    assert.throws(() => assertMatrix(cells), /evidence|zoom|cleanup/);
  }
});
test('matrix rejects unchanged inner geometry or changed outer geometry at browser zoom', () => {
  const cells = complete();
  cells[1].changedGeometry.innerWidth = cells[1].viewport.width;
  assert.throws(() => assertMatrix(cells), /geometry/);
  const outer = complete();
  outer[1].changedGeometry.outerWidth += 1;
  assert.throws(() => assertMatrix(outer), /geometry/);
});
test('F4 rejects incomplete/nonfinite geometry and native sentinel as API receipt', () => {
  for (const mutate of [
    c => { delete c.changedGeometry.innerWidth; },
    c => { c.changedGeometry.innerHeight = NaN; },
    c => { delete c.nativeGeometry.outerWidth; delete c.changedGeometry.outerWidth; },
    c => { c.receipt.tabId = -1; },
    c => { c.receipt.scope = 'host native'; },
  ]) {
    const cells = complete();
    mutate(cells[1]);
    assert.throws(() => assertMatrix(cells), /geometry|zoom/);
  }
});
test('F4 rejects unstructured identity, missing reading and decoded outcome evidence', () => {
  const unstructured = complete();
  unstructured[0].identity = 'PUB/test';
  assert.throws(() => assertMatrix(unstructured), /task|identity|reading|download/);
  for (const mutate of [
    c => { delete c.reading; }, c => { delete c.download; },
    c => { c.identity.snapshotId = 'wrong'; }, c => { c.identity.targets = []; },
    c => { c.download.verified = false; }, c => { c.identity.kind = 'LOCAL'; },
  ]) {
    const cells = complete(); mutate(cells[0]);
    assert.throws(() => assertMatrix(cells), /task|identity|reading|download/);
  }
  const cells = complete(); const review = cells.find(c => c.surface === 'review');
  review.readback.recordId = 'wrong';
  assert.throws(() => assertMatrix(cells), /readback/);
});
test('F4 run evidence requires exact three builds and source/lock/tools/browser bindings', () => {
  assert.equal(typeof support.assertRunEvidence, 'function', 'run binding validator missing');
  const fixture = () => {
    const source = [{ path: 'web/package-lock.json', sha256: hash }, { path: 'web/src/App.tsx', sha256: hash },
      { path: 'web/package.json', sha256: hash }];
    const tools = { node: 'v24.14.0', playwright: '1.63.0', typescript: '5.9.3', vite: '8.2.0' };
    const lockPackages = Object.fromEntries(['playwright', 'typescript', 'vite'].map(n => [`node_modules/${n}`, { version: tools[n] }]));
    const lockBytes = Buffer.from(JSON.stringify({ packages: lockPackages }));
    source[0].sha256 = createHash('sha256').update(lockBytes).digest('hex');
    const builds = Object.entries(support.variants).map(([variant, flags]) => {
      const assets = [{ path: `${variant}/index.html`, sha256: hash }, { path: `${variant}/app.js`, sha256: hash }];
      return { variant, flags: { ...Object.fromEntries(['RESET_WORKBENCH', 'PUBLIC_SHARING', 'PRIVATE_REVIEW', 'PRIVATE_AI']
        .map((n, i) => [`VITE_${n}`, String(flags[i])])), DRAGON_API_PROXY: 'http://127.0.0.1:1' },
        sourceSha256: support.digest(source), lockSha256: source[0].sha256, tools, assets, assetsSha256: support.digest(assets) };
    });
    const cells = complete().map(c => ({ ...c,
      receipt: { ...c.receipt, appUrl: `http://127.0.0.1:${{ public: 4171, local: 4172, owner: 4173 }[c.variant]}/` },
      build: { variant: c.variant, assetsSha256: builds.find(b => b.variant === c.variant).assetsSha256 } }));
    return { source, builds, tools, cells, browser: '153.0.8010.12',
      readBytes: path => path === 'web/package-lock.json' ? lockBytes : Buffer.from('fixture'), lockPackages };
  };
  support.assertRunEvidence(fixture());
  const measuredNode22 = fixture();
  measuredNode22.tools.node = 'v22.23.5';
  support.assertRunEvidence(measuredNode22);
  for (const node of ['v20.0.0', 'v22.23.4', 'v22.23.6', 'v24.14.1']) {
    const unsupported = fixture();
    unsupported.tools.node = node;
    assert.throws(() => support.assertRunEvidence(unsupported), /Node binding mismatch/);
  }
  const mismatchedBuild = fixture();
  mismatchedBuild.tools.node = 'v22.23.5';
  mismatchedBuild.builds[0].tools = { ...mismatchedBuild.tools, node: 'v24.14.0' };
  assert.throws(() => support.assertRunEvidence(mismatchedBuild), /build tool binding mismatch/);
  for (const mutate of [
    r => { r.builds.pop(); }, r => { r.builds[1] = r.builds[0]; },
    r => { r.builds[0].flags.VITE_PUBLIC_SHARING = '0'; },
    r => { r.builds[0].sourceSha256 = hash; }, r => { delete r.builds[0].lockSha256; },
    r => { r.tools.node = 'v20.0.0'; }, r => { r.browser = 'fake'; },
    r => { r.cells[0].build.assetsSha256 = hash; }, r => { r.cells[0].receipt.appUrl = 'http://127.0.0.1:4172/'; },
    r => { r.builds[0].assets.pop(); },
    r => { r.tools.typescript = 'locked'; }, r => { r.tools.vite = 'wrong'; },
    r => { r.readBytes = () => Buffer.from('tampered'); }, r => { delete r.readBytes; },
    r => { r.source[0].sha256 = 'b'.repeat(64); },
    r => { r.lockPackages['node_modules/vite'].version = 'fake'; },
  ]) { const evidence = fixture(); mutate(evidence); assert.throws(() => support.assertRunEvidence(evidence)); }
});
test('F6 traffic receipts must bind exact request and completed assertion', () => {
  assert.equal(typeof support.createTrafficAudit, 'function', 'exact receipt audit missing');
  const audit = support.createTrafficAudit();
  const a = {}, b = {};
  const receipt = audit.expectRequest(a, { url: 'http://127.0.0.1/api/search?q=A', method: 'GET',
    kind: 'denial', status: 403, reason: 'source withdrawal' });
  audit.response(a, 403);
  assert.throws(() => audit.assertClean(), /assertion/);
  receipt.asserted('refusal visible and protected targets cleared');
  audit.assertClean();
  audit.response(b, 403);
  assert.throws(() => audit.assertClean(), /unexpected/);
  const cancel = support.createTrafficAudit();
  const c = {}, d = {};
  const canceled = cancel.expectRequest(c, { url: '/api/search?q=A', method: 'GET',
    kind: 'cancel', reason: 'closed pending search' });
  cancel.failed(c, 'net::ERR_ABORTED'); canceled.asserted('closed and late release settled');
  cancel.assertClean();
  cancel.failed(d, 'net::ERR_ABORTED');
  assert.throws(() => cancel.assertClean(), /unexpected/);
  const lock = support.createTrafficAudit(), session = {};
  const denied = lock.expectRequest(session, { url: '/api/owner/v1/session', method: 'GET', kind: 'denial',
    status: 401, reason: 'owner lock', allowAbortAfterDenial: true });
  lock.response(session, 401);
  lock.failed(session, 'net::ERR_ABORTED');
  denied.asserted('owner lock cleared active protected content');
  lock.assertClean();
  lock.failed({}, 'net::ERR_ABORTED');
  assert.throws(() => lock.assertClean(), /unexpected/);
});
test('F7 postlaunch setup failure closes context and deletes owned profile', async () => {
  assert.equal(typeof support.protectSetup, 'function', 'postlaunch cleanup protection missing');
  const events = [];
  await assert.rejects(support.protectSetup(async () => { throw new Error('worker timeout'); },
    async () => events.push('closed'), () => events.push('removed')), /worker timeout/);
  assert.deepEqual(events, ['closed', 'removed']);
  const failures = [];
  await assert.rejects(support.protectSetup(async () => { throw new Error('init script'); },
    async () => { failures.push('close'); throw new Error('close failed'); }, () => failures.push('remove')),
  /setup\/cleanup/);
  assert.deepEqual(failures, ['close', 'remove']);
});
test('F7 passed result is impossible until required cleanup is verified', () => {
  assert.equal(typeof support.finalResult, 'function', 'cleanup-dependent final result missing');
  assert.equal(support.finalResult({ outcome: 'passed' }, { containerRemoved: false }).outcome, 'failed');
  assert.equal(support.finalResult({ outcome: 'passed' }, { containerRemoved: true }).outcome, 'passed');
});
test('F3 structural CSV parsing preserves quoted metadata, commas, newlines and duplicate rows', () => {
  assert.equal(typeof support.parseCsv, 'function', 'structural CSV parser missing');
  assert.deepEqual(support.parseCsv('id,filters,text\r\nA,"{""q"":""x,y""}","line1\nline2"\r\nA,{},plain\r\n'),
    [{ id: 'A', filters: '{"q":"x,y"}', text: 'line1\nline2' }, { id: 'A', filters: '{}', text: 'plain' }]);
  assert.throws(() => support.parseCsv('id,text\r\nA,"unfinished'), /CSV/);
  assert.throws(() => support.parseCsv('id,text\r\nA\r\n'), /CSV/);
});
test('F4 rejects wrong actual targets, unparsed downloads, mismatched LOCAL content and unlocked tools', () => {
  for (const mutate of [
    c => { c.identity.targets = ['WRONG']; },
    c => { c.download.decoded = 'decoded'; },
    c => { c.download.rowCount += 1; },
  ]) {
    const cells = complete(); mutate(cells[0]);
    assert.throws(() => assertMatrix(cells), /identity|download/);
  }
  const cells = complete(), local = cells.find(c => c.surface === 'review');
  local.download.decoded = JSON.stringify({ notes: [], revisions: [] });
  assert.throws(() => assertMatrix(cells), /LOCAL|download/);
  const readback = complete(), review = readback.find(c => c.surface === 'review');
  const backup = JSON.parse(review.download.decoded);
  backup.revisions[0].content.title = 'wrong';
  review.download.decoded = JSON.stringify(backup);
  assert.throws(() => assertMatrix(readback), /LOCAL|download/);
  const omitted = complete();
  omitted[0].download.expectedRows = omitted[0].download.expectedRows.slice(1);
  assert.throws(() => assertMatrix(omitted), /download/);
});
