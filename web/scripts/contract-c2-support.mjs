import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';

export const publication = { snapshotId: '00000000-0000-4000-8000-000000000001', date: '2026-09-10' };
export const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
export const variants = {
  public: [1, 1, 0, 0],
  local: [1, 0, 0, 0],
  owner: [1, 0, 1, 1],
};
export const surfaces = ['overview', 'news', 'leaders', 'analysis', 'stock', 'regulation', 'review'];
const security = n => `TEST${String(n).padStart(3, '0')}`;
export const targetSets = {
  overview: Array.from({ length: 12 }, (_, i) => security(23 - i * 2)),
  news: Array.from({ length: 5 }, (_, i) => `00000000-0000-4000-8000-${String(319 + i).padStart(12, '0')}`),
  leaders: Array.from({ length: 5 }, (_, i) => security(21 + i)),
  analysis: Array.from({ length: 5 }, (_, i) => security(21 + i)),
  stock: ['TEST001'],
  regulation: ['pending', 'idle', 'approaching', 'triggered'].map(s => `calculation/synthetic-${s}/TEST001`)
    .concat(Array(4).fill('event/synthetic-formal-event/TEST001')),
};
export const metadataColumns = ['snapshot_id', 'trade_date', 'as_of', 'rule_version', 'filters',
  'unit', 'currency', 'price_type', 'benchmark'];
export const dataColumns = {
  overview: ['security_id', 'code', 'name', 'direction'],
  news: ['article_id', 'article_type', 'source', 'source_version', 'title', 'published_at', 'available_at', 'revision'],
  leaders: ['rank', 'security_id', 'name', 'sector_id', 'boards', 'evidence_status'],
  analysis: ['rank', 'security_id', 'name', 'topics', 'return_ratio', 'excess_return_ratio', 'max_drawdown_ratio', 'amount_change_ratio'],
  stock: ['record_type', 'security_id', 'row_trade_date', 'last', 'change_ratio', 'open', 'high', 'low', 'close', 'volume', 'amount', 'roles', 'formal_event_count'],
  regulation: ['record_type', 'rule_id', 'record_rule_version', 'security_id', 'state', 'value', 'effective_from', 'effective_to'],
};
export const viewports = [
  { width: 375, height: 812 }, { width: 768, height: 1024 },
  { width: 1024, height: 768 }, { width: 1440, height: 1000 },
];
export function expectedCells() {
  return Object.keys(variants).flatMap(variant => surfaces
    .filter(surface => variant !== 'public' || surface !== 'review')
    .flatMap(surface => viewports.flatMap(viewport => ['light', 'dark'].flatMap(theme =>
      [1, 2].map(zoom => ({ variant, surface, viewport, theme, zoom }))))));
}
export function cellKey(cell) {
  return [cell.variant, cell.surface, cell.viewport.width, cell.viewport.height, cell.theme, cell.zoom].join('/');
}
export function assertMatrix(cells) {
  const expected = new Set(expectedCells().map(cellKey));
  const seen = new Set();
  for (const cell of cells) {
    const key = cellKey(cell);
    if (!expected.has(key)) throw new Error(`unexpected matrix cell: ${key}`);
    if (seen.has(key)) throw new Error(`duplicate matrix cell: ${key}`);
    seen.add(key);
    if (cell.measured?.width !== cell.viewport.width || cell.measured?.height !== cell.viewport.height) {
      throw new Error(`viewport evidence mismatch: ${key}`);
    }
    if (![cell.nativeGeometry, cell.changedGeometry].every(g => g &&
      ['innerWidth', 'innerHeight', 'outerWidth', 'outerHeight'].every(k => Number.isFinite(g[k]) && g[k] > 0))
      || cell.nativeGeometry?.innerWidth !== cell.viewport.width || cell.nativeGeometry?.innerHeight !== cell.viewport.height
      || Math.abs(cell.changedGeometry?.innerWidth - cell.viewport.width / cell.zoom) > 0.51
      || Math.abs(cell.changedGeometry?.innerHeight - cell.viewport.height / cell.zoom) > 0.51
      || cell.changedGeometry?.outerWidth !== cell.nativeGeometry?.outerWidth
      || cell.changedGeometry?.outerHeight !== cell.nativeGeometry?.outerHeight) {
      throw new Error(`browser geometry evidence mismatch: ${key}`);
    }
    if (cell.receipt?.requested !== cell.zoom || cell.receipt?.actual !== cell.zoom
      || !Number.isInteger(cell.receipt?.tabId) || cell.receipt.tabId <= 0
      || cell.receipt.scope !== 'chrome.tabs' || !/^http:\/\/127\.0\.0\.1:417[123]\//.test(cell.receipt.appUrl)
      || cell.receipt.browser !== '153.0.8010.12') throw new Error(`zoom evidence missing: ${key}`);
    if (cell.outcome !== 'passed' || !cell.operation || !cell.identity) throw new Error(`task evidence missing: ${key}`);
    const identity = cell.identity;
    if (identity.snapshotId !== publication.snapshotId || identity.date !== publication.date
      || identity.kind !== (cell.surface === 'review' ? 'LOCAL' : 'PUB')
      || (cell.surface !== 'review' && (!Array.isArray(identity.targets) || !identity.targets.length
        || !identity.targets.every(t => typeof t === 'string' && t.length)))) throw new Error(`task identity missing: ${key}`);
    if (!cell.download?.filename || typeof cell.download.decoded !== 'string' || !cell.download.decoded
      || cell.download.verified !== true || !Number.isInteger(cell.download.rowCount) || cell.download.rowCount < 1) {
      throw new Error(`download task evidence missing: ${key}`);
    }
    if (cell.surface === 'review' && (!identity.recordId || !Number.isInteger(identity.revision)
      || identity.revision < 1 || cell.readback?.recordId !== identity.recordId
      || cell.readback?.revision !== identity.revision || !cell.readback.title || !cell.readback.body
      || cell.readback.reloaded !== true)) throw new Error(`LOCAL readback evidence missing: ${key}`);
    if (cell.surface === 'review') {
      const backup = JSON.parse(cell.download.decoded);
      assert.equal(backup.format_version, 1, 'LOCAL download format mismatch');
      assert.equal(backup.notes?.length, 1, 'LOCAL download note count mismatch');
      assert.equal(backup.revisions?.length, 1, 'LOCAL download revision count mismatch');
      const note = backup.notes[0], revision = backup.revisions[0];
      assert.deepEqual([note.note_id, note.current_revision, note.snapshot_id, note.trade_date],
        [identity.recordId, identity.revision, identity.snapshotId, identity.date], 'LOCAL download identity mismatch');
      assert.deepEqual([revision.note_id, revision.revision, revision.content?.title, revision.content?.content],
        [identity.recordId, identity.revision, cell.readback.title, cell.readback.body], 'LOCAL readback/download mismatch');
      assert.equal(cell.download.rowCount, 1, 'LOCAL download count mismatch');
      assert.match(cell.download.filename, /^dragon-radar-review-\d{4}-\d{2}-\d{2}\.json$/, 'LOCAL download filename');
    } else {
      assert.deepEqual(identity.targets, targetSets[cell.surface], `task identity mismatch: ${key}`);
      const rows = parseCsv(cell.download.decoded);
      assert.deepEqual(Object.keys(rows[0] || {}), [...metadataColumns, ...dataColumns[cell.surface]], 'download schema mismatch');
      assert.equal(rows.length, cell.download.rowCount, 'download count mismatch');
      assert.deepEqual(rows, cell.download.expectedRows, 'download parsed outcome mismatch');
      assert(rows.every(r => r.snapshot_id === identity.snapshotId && r.trade_date === identity.date), 'download PUB mismatch');
      const targets = cell.surface === 'news' ? rows.map(r => r.article_id) : cell.surface === 'regulation'
        ? rows.map(r => `${r.record_type}/${r.rule_id}/${r.security_id}`) : cell.surface === 'stock'
          ? [...new Set(rows.map(r => r.security_id))] : rows.map(r => r.security_id);
      assert.deepEqual(targets, identity.targets, 'download target identity mismatch');
      assert.equal(rows.length, cell.surface === 'stock' ? 22 : identity.targets.length, 'download raw-window count mismatch');
      assert.match(cell.download.filename, new RegExp(`^dragon-radar-${{
        overview: 'overview-current-filter', news: 'news-current-page', leaders: 'leaders-current-filter',
        analysis: 'analysis-current-page', stock: 'stock-current-filter', regulation: 'regulation-current-filter',
      }[cell.surface]}-${publication.date}-00000000\\.csv$`), 'download filename scope mismatch');
    }
    const reading = cell.reading;
    if (!reading?.text || !reading.color || !reading.background || !Number.isFinite(reading.contrast) || reading.contrast < 4.5
      || !Number.isFinite(reading.rootWidth) || reading.rootWidth <= 0 || !Number.isFinite(reading.rootScroll)
      || reading.rootScroll > reading.rootWidth + 1 || !Array.isArray(reading.tables) || !Array.isArray(reading.scrollProof)
      || reading.tables.some(t => !Number.isFinite(t.width) || !Number.isFinite(t.scroll)
        || t.scroll > t.width + 1 && !reading.scrollProof.some(p => p.label === t.label && p.left > 0
          && p.width === t.width && p.scroll === t.scroll))) throw new Error(`reading evidence missing: ${key}`);
    if (cell.build?.variant !== cell.variant || !/^[a-f0-9]{64}$/.test(cell.build.assetsSha256)) {
      throw new Error(`build evidence missing: ${key}`);
    }
    if (cell.cleanup?.restored !== 1 || cell.cleanup?.closed !== true || cell.cleanup?.profileRemoved !== true) {
      throw new Error(`cleanup evidence missing: ${key}`);
    }
  }
  if (seen.size !== expected.size) throw new Error(`missing cells: count ${seen.size}/${expected.size}`);
  return { cells: seen.size, pairs: seen.size / 2 };
}

export function assertBuildEvidence({ builds, source, tools, lockPackages, readBytes }) {
  assert(Array.isArray(source) && source.length > 2, 'source binding missing');
  assert(source.every(s => s.path && /^[a-f0-9]{64}$/.test(s.sha256)), 'source hashes missing');
  const lock = source.find(s => s.path === 'web/package-lock.json');
  assert(lock, 'lock binding missing');
  assert(['v24.14.0', 'v22.23.5'].includes(tools?.node), 'Node binding mismatch');
  assert.equal(tools?.playwright, '1.63.0', 'Playwright binding mismatch');
  for (const name of ['playwright', 'typescript', 'vite']) {
    assert.equal(tools?.[name], lockPackages?.[`node_modules/${name}`]?.version, `${name} lock/tool binding mismatch`);
  }
  assert.equal(typeof readBytes, 'function', 'raw source/build verifier missing');
  const verifyFiles = files => {
    assert.equal(new Set(files.map(f => f.path)).size, files.length, 'duplicate raw file binding');
    for (const file of files) {
      const bytes = readBytes(file.path);
      if (Object.hasOwn(file, 'size')) assert.equal(bytes.length, file.size, `raw file size mismatch: ${file.path}`);
      assert.equal(createHash('sha256').update(bytes).digest('hex'), file.sha256,
        `raw file binding mismatch: ${file.path}`);
    }
  };
  verifyFiles(source);
  assert.deepEqual(JSON.parse(readBytes(lock.path).toString()).packages, lockPackages, 'raw lock/tool binding mismatch');
  assert.deepEqual(builds?.map(b => b.variant).sort(), Object.keys(variants).sort(), 'exact three builds required');
  for (const build of builds) {
    const expectedFlags = Object.fromEntries(['RESET_WORKBENCH', 'PUBLIC_SHARING', 'PRIVATE_REVIEW', 'PRIVATE_AI']
      .map((name, i) => [`VITE_${name}`, String(variants[build.variant][i])]));
    assert.deepEqual(build.flags, { ...expectedFlags, DRAGON_API_PROXY: 'http://127.0.0.1:1' }, 'build flags mismatch');
    assert.equal(build.sourceSha256, digest(source), 'build source binding mismatch');
    assert.equal(build.lockSha256, lock.sha256, 'build lock binding mismatch');
    assert.deepEqual(build.tools, tools, 'build tool binding mismatch');
    assert(Array.isArray(build.assets) && build.assets.length >= 2, 'build assets missing');
    assert(build.assets.every(a => a.path && /^[a-f0-9]{64}$/.test(a.sha256)), 'asset hashes missing');
    assert.equal(build.assetsSha256, digest(build.assets), 'asset set binding mismatch');
    verifyFiles(build.assets);
  }
}

export function assertRunEvidence(evidence) {
  assertBuildEvidence(evidence);
  const { builds, browser, cells } = evidence;
  assert.equal(browser, '153.0.8010.12', 'browser binding mismatch');
  for (const cell of cells) {
    const build = builds.find(b => b.variant === cell.variant);
    assert.equal(cell.build?.assetsSha256, build.assetsSha256, 'cell build binding mismatch');
    assert.equal(cell.receipt?.browser, browser, 'cell browser binding mismatch');
    assert.equal(new URL(cell.receipt.appUrl).port, { public: '4171', local: '4172', owner: '4173' }[cell.variant],
      'app tab variant binding mismatch');
  }
  return assertMatrix(cells);
}

export function createTrafficAudit() {
  const expected = new Map(), errors = [], receipts = [];
  return {
    errors, receipts,
    expectRequest(request, detail) {
      assert(!expected.has(request), 'request already classified');
      assert(detail.url && detail.method && detail.reason && ['cancel', 'denial'].includes(detail.kind), 'exact request reason missing');
      const receipt = { ...detail, observed: false, assertion: null };
      expected.set(request, receipt); receipts.push(receipt);
      return { asserted: assertion => { assert(assertion, 'required assertion missing'); receipt.assertion = assertion; } };
    },
    response(request, status) {
      if (status < 400) return;
      const receipt = expected.get(request);
      if (!receipt || receipt.kind !== 'denial' || receipt.status !== status || receipt.observed) {
        errors.push(`unexpected HTTP ${status}`); return;
      }
      receipt.observed = true;
    },
    failed(request, reason) {
      const receipt = expected.get(request);
      if (receipt?.kind === 'denial' && receipt.allowAbortAfterDenial === true && receipt.observed
        && !receipt.abortedAfterDenial && reason === 'net::ERR_ABORTED') {
        receipt.abortedAfterDenial = true;
        return;
      }
      if (!receipt || receipt.kind !== 'cancel' || receipt.observed || reason !== 'net::ERR_ABORTED') {
        errors.push(`unexpected request failure: ${reason} ${request.url?.() || ''}`); return;
      }
      receipt.observed = true;
    },
    assertClean() {
      assert.deepEqual(errors, [], 'unexpected traffic');
      assert(receipts.every(r => r.observed && r.assertion), 'receipt missing observation or required assertion');
    },
  };
}

export async function protectSetup(setup, close, remove) {
  try { return await setup(); }
  catch (error) {
    const failures = [error];
    try { await close(); } catch (failure) { failures.push(failure); }
    try { remove(); } catch (failure) { failures.push(failure); }
    if (failures.length > 1) throw new AggregateError(failures, 'C2 postlaunch setup/cleanup failed');
    throw error;
  }
}
export function finalResult(result, cleanup, failure) {
  const cleaned = cleanup.containerRemoved === true || cleanup.hostProfilesVerified === true;
  return { ...result, outcome: !failure && cleaned ? 'passed' : 'failed', cleanup,
    ...(failure ? { error: String(failure) } : {}) };
}
export function parseCsv(text) {
  const records = [], row = [];
  let value = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') {
      if (quoted && text[i + 1] === '"') { value += '"'; i++; }
      else quoted = !quoted;
    } else if (!quoted && (c === ',' || c === '\r' || c === '\n')) {
      row.push(value); value = '';
      if (c !== ',') {
        records.push([...row]); row.length = 0;
        if (c === '\r' && text[i + 1] === '\n') i++;
      }
    } else value += c;
  }
  assert(!quoted, 'CSV unterminated quoted field');
  if (value || row.length) { row.push(value); records.push([...row]); }
  const headers = records.shift();
  assert(headers?.length && new Set(headers).size === headers.length, 'CSV headers invalid');
  return records.map(record => {
    assert.equal(record.length, headers.length, 'CSV row width mismatch');
    return Object.fromEntries(headers.map((key, i) => [key, record[i]]));
  });
}
