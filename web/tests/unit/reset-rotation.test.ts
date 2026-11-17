import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, request } from '../../src/api';
import {
  ROTATION_FIRST_OBSERVATION,
  ROTATION_MEMBER_POOL_CHANGED,
  ROTATION_MEMBERSHIP_VERSION_CHANGED,
  ROTATION_NO_CORE_CANDIDATE,
  ROTATION_NOT_PREVIOUS_COMPARABLE_SESSION,
  ROTATION_PREVIOUS_COMPARABLE_RUN_MISMATCH,
  ROTATION_RULE_VERSION_CHANGED,
  ROTATION_TAXONOMY_VERSION_CHANGED,
  ROTATION_TOPIC_REVISION_CHANGED,
  rotationPath,
  validateRotation,
} from '../../src/features/roles/api';
import { reasonLabel, rotationChangeLabel } from '../../src/features/roles/labels';
import { SectionLoadError } from '../../src/features/roles/SectionBoundary';
import {
  groupRotationLanes,
  RotationTimelineView,
  type RotationNodeSelection,
} from '../../src/features/roles/RotationTimeline';
import type {
  RotationChange,
  RotationData,
  RotationEvent,
  RotationResponse,
} from '../../src/features/roles/types';

const DAY1 = '2026-09-04';
const DAY2 = '2026-09-07';
const DATE = '2026-09-08';
const SNAPSHOT = '00000000-0000-4000-8000-0000000000ab';
const HIST_SNAPSHOT = '00000000-0000-4000-8000-0000000000cd';
const MARKET_UUID = '11111111-1111-4111-8111-aaaaaaaaaaaa';
const TOPIC_UUID = '22222222-2222-4222-a222-bbbbbbbbbbbb';
const selection = { snapshot: SNAPSHOT, date: DATE };

afterEach(() => vi.unstubAllGlobals());

const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

function makeEvent(
  at: string,
  sectorId: string,
  coreIds: string[],
  change: RotationChange,
  comparable: boolean,
  reason: string | null,
  snapshotId: string,
): RotationEvent {
  return {
    at,
    sector_id: sectorId,
    core_ids: coreIds,
    change,
    comparable,
    reason,
    snapshot_id: snapshotId,
    input_manifest: {
      run_id: 'role-run-1',
      market_snapshot_id: MARKET_UUID,
      topic_snapshot_id: TOPIC_UUID,
      rule_version: 'role-v1',
      membership_version: 'm1',
    },
  };
}

// Three days, two sectors; anchor-day events carry the anchor publication id
// while historical days carry the publication that served that day.
function makeEvents(): RotationEvent[] {
  return [
    makeEvent(DAY1, 'topic-a', ['A'], 'segment_start', false, 'first_observation', HIST_SNAPSHOT),
    makeEvent(DAY1, 'topic-b', ['C'], 'segment_start', false, 'first_observation', HIST_SNAPSHOT),
    makeEvent(DAY2, 'topic-a', ['A', 'B'], 'expanded', true, null, HIST_SNAPSHOT),
    makeEvent(DAY2, 'topic-b', [], 'unchanged', true, 'no_core_candidate', HIST_SNAPSHOT),
    makeEvent(DATE, 'topic-a', ['B'], 'contracted', true, null, SNAPSHOT),
    makeEvent(DATE, 'topic-b', ['C', 'D'], 'reorganized', true, null, SNAPSHOT),
  ];
}

function makeData(events: RotationEvent[] = makeEvents(), over: Record<string, unknown> = {}): RotationData {
  return {
    events,
    days: 30,
    sector: null,
    window: { start: DAY1, end: DATE },
    ...over,
  } as RotationData;
}

function makeEnvelope(data: RotationData | null, over: Record<string, unknown> = {}): RotationResponse {
  return {
    trade_date: DATE,
    as_of: '2026-09-08T10:00:00Z',
    snapshot_id: SNAPSHOT,
    source: 'role_materialization',
    source_version: 'role-source-v1',
    coverage: { expected: 1, valid: 1, excluded: 0, missing: 0 },
    quality_flags: [],
    rule_version: 'role-v1',
    basis: { unit: null, currency: null, price_type: null, benchmark: 'CSI300' },
    data,
    ...over,
  } as RotationResponse;
}

function validate(value: unknown, sel: { snapshot: string; date: string; sector?: string } = selection, days = 30) {
  return validateRotation(value, sel, days);
}

function withEvent(
  index: number,
  patch: (event: Record<string, unknown>) => void,
  dataOver: Record<string, unknown> = {},
  sel = selection,
  days = 30,
) {
  const events = clone(makeEvents());
  patch(events[index] as unknown as Record<string, unknown>);
  return validate(makeEnvelope(makeData(events, dataOver)), sel, days);
}

function withManifest(index: number, patch: (manifest: Record<string, unknown>) => void) {
  return withEvent(index, event => {
    patch(event.input_manifest as Record<string, unknown>);
  });
}

function expectRejected(call: () => unknown, expectedStatus: number) {
  let caught: unknown;
  try {
    call();
  } catch (error) {
    caught = error;
  }
  expect(caught).toBeInstanceOf(ApiError);
  expect((caught as ApiError).status).toBe(expectedStatus);
}

describe('rotationPath', () => {
  it('serializes date, snapshot id and the default 30 day window without a sector', () => {
    expect(rotationPath({ date: DATE, snapshot: SNAPSHOT, days: 30 }))
      .toBe(`/api/topics/rotation?date=${DATE}&snapshot_id=${SNAPSHOT}&days=30`);
    const url = new URL(rotationPath({ date: DATE, snapshot: SNAPSHOT, days: 30 }), 'https://example.test');
    expect(url.searchParams.get('sector')).toBeNull();
  });

  it('encodes the sector filter and custom day windows', () => {
    const path = rotationPath({ date: DATE, snapshot: SNAPSHOT, sector: 'topic-a', days: 120 });
    const url = new URL(path, 'https://example.test');
    expect(Object.fromEntries(url.searchParams)).toEqual({
      date: DATE,
      snapshot_id: SNAPSHOT,
      days: '120',
      sector: 'topic-a',
    });
  });
});

describe('validateRotation', () => {
  it('accepts the valid three day two sector timeline', () => {
    const response = makeEnvelope(makeData());
    expect(validate(response)).toBe(response);
  });

  it('accepts the domain-unavailable null envelope and an empty event window', () => {
    expect(() => validate(makeEnvelope(null))).not.toThrow();
    expect(() => validate(makeEnvelope(makeData([])))).not.toThrow();
  });

  it('binds anchor-day events to the envelope publication id', () => {
    const events = makeEvents();
    // The historical day keeps its own serving id; only anchor-day binding
    // is enforced by the client.
    expect(events[0].snapshot_id).not.toBe(SNAPSHOT);
    expect(() => validate(makeEnvelope(makeData(events)))).not.toThrow();
  });

  it.each([
    ['envelope snapshot drift', makeEnvelope(makeData(), { snapshot_id: HIST_SNAPSHOT }), 409],
    ['envelope date drift', makeEnvelope(makeData(), { trade_date: DAY2 }), 409],
    ['sector filter not echoed', makeEnvelope(makeData(), {}), 409],
  ])('maps %s to a selection ApiError', (_name, body, expectedStatus) => {
    const sel = _name === 'sector filter not echoed'
      ? { snapshot: SNAPSHOT, date: DATE, sector: 'topic-a' }
      : selection;
    expectRejected(() => validate(body, sel), expectedStatus);
  });

  it('reports an echoed sector that differs from the request as 409', () => {
    expectRejected(() => withEvent(4, () => {}, { sector: 'topic-z' }), 409);
  });

  it('reports a days echo that differs from the request as a malformed 502', () => {
    expectRejected(() => withEvent(4, () => {}, { days: 60 }, selection, 30), 502);
  });

  it.each<[string, number, () => unknown]>([
    ['change 不属于五枚举', 502, () => withEvent(2, event => { event.change = 'moon'; })],
    ['comparable 是数字 1', 502, () => withEvent(2, event => { event.comparable = 1; })],
    ['comparable 是数字 0', 502, () => withEvent(0, event => { event.comparable = 0; })],
    ['comparable 是字符串', 502, () => withEvent(2, event => { event.comparable = 'true'; })],
    ['segment_start 标记为可比', 502, () => withEvent(0, event => { event.comparable = true; })],
    ['可比事件标记为 segment_start', 502, () => withEvent(2, event => { event.change = 'segment_start'; })],
    ['可比事件携带意外 reason', 502, () => withEvent(2, event => { event.reason = 'unexpected'; })],
    ['可比空核未声明无核心候选', 502, () => withEvent(3, event => { event.reason = null; })],
    ['可比有核却声明无核心候选', 502, () => withEvent(2, event => { event.reason = 'no_core_candidate'; })],
    ['不可比事件缺少 reason', 502, () => withEvent(0, event => { event.reason = null; })],
    ['不可比 reason 不在白名单', 502, () => withEvent(0, event => { event.reason = 'mystery_token'; })],
    ['事件缺少键', 502, () => withEvent(2, event => { delete event.change; })],
    ['事件包含未知键', 502, () => withEvent(2, event => { event.extra = 1; })],
    ['manifest 缺少键', 502, () => withManifest(2, manifest => { delete manifest.rule_version; })],
    ['manifest 包含未知键', 502, () => withManifest(2, manifest => { manifest.extra = 1; })],
    ['manifest run_id 为空串', 502, () => withManifest(2, manifest => { manifest.run_id = ''; })],
    ['manifest market_snapshot_id 大写', 502, () => withManifest(2, manifest => {
      manifest.market_snapshot_id = MARKET_UUID.toUpperCase();
    })],
    ['manifest market_snapshot_id 非法', 502, () => withManifest(2, manifest => {
      manifest.market_snapshot_id = 'not-a-uuid';
    })],
    ['manifest topic_snapshot_id 非法', 502, () => withManifest(2, manifest => {
      manifest.topic_snapshot_id = 'x';
    })],
    ['锚点事件 snapshot_id 大写', 502, () => withEvent(4, event => {
      event.snapshot_id = SNAPSHOT.toUpperCase();
    })],
    ['事件 snapshot_id 不是 UUID', 502, () => withEvent(4, event => { event.snapshot_id = 'not-a-uuid'; })],
    ['锚点事件偷换为历史发布', 502, () => withEvent(4, event => { event.snapshot_id = HIST_SNAPSHOT; })],
    ['事件乱序', 502, () => {
      const events = makeEvents();
      [events[0], events[1]] = [events[1], events[0]];
      return validate(makeEnvelope(makeData(events)));
    }],
    ['(at, sector) 重复', 502, () => {
      const events = makeEvents();
      events.push(clone(events[4]));
      return validate(makeEnvelope(makeData(events)));
    }],
    ['事件 at 越出窗口', 502, () => withEvent(0, event => { event.at = '2026-09-03'; })],
    ['事件 at 不是日历日期', 502, () => withEvent(0, event => { event.at = '2026-9-4'; })],
    ['sector_id 为空串', 502, () => withEvent(2, event => { event.sector_id = ''; })],
    ['window.end 与请求日期不符', 502, () => withEvent(2, () => {}, {
      window: { start: DAY1, end: '2026-09-09' },
    })],
    ['window.start 晚于 end', 502, () => withEvent(2, () => {}, {
      window: { start: '2026-09-09', end: DATE },
    })],
    ['window 包含未知键', 502, () => withEvent(2, () => {}, {
      window: { start: DAY1, end: DATE, extra: 1 },
    })],
    ['data 包含未知键', 502, () => withEvent(2, () => {}, { extra: 1 })],
    ['data 缺少 days 键', 502, () => {
      const value = makeData() as unknown as Record<string, unknown>;
      delete value.days;
      return validate(makeEnvelope(value as unknown as RotationData));
    }],
    ['events 不是数组', 502, () => withEvent(2, () => {}, { events: {} })],
    ['days 为 0', 502, () => withEvent(2, () => {}, { days: 0 }, selection, 0)],
    ['days 超过 250', 502, () => withEvent(2, () => {}, { days: 251 }, selection, 251)],
    ['days 不是整数', 502, () => withEvent(2, () => {}, { days: 1.5 }, selection, 1.5)],
    ['core_ids 重复', 502, () => withEvent(2, event => { event.core_ids = ['A', 'A']; })],
    ['core_ids 含非字符串', 502, () => withEvent(2, event => { event.core_ids = ['A', 2]; })],
    ['core_ids 含空串', 502, () => withEvent(2, event => { event.core_ids = ['']; })],
  ])('%s 时丢弃该响应', (_name, expectedStatus, call) => {
    expectRejected(call, expectedStatus);
  });

  it('rejects an event whose at is later than window.end', () => {
    expectRejected(() => withEvent(5, event => { event.at = '2026-09-09'; }), 502);
  });

  it('accepts a sector-filtered request whose data echoes the same sector', () => {
    const filtered = makeEvents().filter(event => event.sector_id === 'topic-a');
    const body = makeEnvelope(makeData(filtered, { sector: 'topic-a' }));
    expect(() => validate(body, { snapshot: SNAPSHOT, date: DATE, sector: 'topic-a' })).not.toThrow();
  });

  it.each([
    ROTATION_FIRST_OBSERVATION,
    ROTATION_RULE_VERSION_CHANGED,
    ROTATION_TOPIC_REVISION_CHANGED,
    ROTATION_TAXONOMY_VERSION_CHANGED,
    ROTATION_MEMBERSHIP_VERSION_CHANGED,
    ROTATION_MEMBER_POOL_CHANGED,
    ROTATION_PREVIOUS_COMPARABLE_RUN_MISMATCH,
    ROTATION_NOT_PREVIOUS_COMPARABLE_SESSION,
    ROTATION_NO_CORE_CANDIDATE,
  ])('accepts non-comparable events carrying frozen reason token %s', token => {
    const events = makeEvents();
    (events[0] as unknown as Record<string, unknown>).reason = token;
    expect(() => validate(makeEnvelope(makeData(events)))).not.toThrow();
  });
});

describe('rotation vocabulary labels', () => {
  const REASON_LABELS: ReadonlyArray<readonly [string, string]> = [
    [ROTATION_FIRST_OBSERVATION, '首次观察'],
    [ROTATION_RULE_VERSION_CHANGED, '规则版本已变化'],
    [ROTATION_TOPIC_REVISION_CHANGED, '题材修订已变化'],
    [ROTATION_TAXONOMY_VERSION_CHANGED, '题材分类版本已变化'],
    [ROTATION_MEMBERSHIP_VERSION_CHANGED, '题材成员版本已变化'],
    [ROTATION_MEMBER_POOL_CHANGED, '题材成员池已变化'],
    [ROTATION_PREVIOUS_COMPARABLE_RUN_MISMATCH, '前驱可比运行不匹配'],
    [ROTATION_NOT_PREVIOUS_COMPARABLE_SESSION, '前驱不是相邻可比交易日'],
    [ROTATION_NO_CORE_CANDIDATE, '无核心候选'],
  ];
  const CHANGE_LABELS: ReadonlyArray<readonly [RotationChange, string]> = [
    ['segment_start', '新观察段'],
    ['unchanged', '核心未变'],
    ['expanded', '核心扩大'],
    ['contracted', '核心缩小'],
    ['reorganized', '核心重组'],
  ];

  it.each(REASON_LABELS)('maps frozen reason %s to a Chinese label', (token, label) => {
    const actual = reasonLabel(token);
    expect(actual).toBe(label);
    expect(actual).not.toBe('未识别状态');
  });

  it.each(CHANGE_LABELS)('maps change %s without causal wording', (change, label) => {
    expect(rotationChangeLabel(change)).toBe(label);
    for (const forbidden of ['接替', '接力', '取代', '替代', '易主', '领涨']) {
      expect(label).not.toContain(forbidden);
    }
  });
});

describe('groupRotationLanes', () => {
  it('is deterministic and groups by sector in lexicographic order', () => {
    const events = makeEvents();
    const first = groupRotationLanes(events);
    const second = groupRotationLanes(events);
    expect(second).toEqual(first);
    expect(first.map(lane => lane.sector_id)).toEqual(['topic-a', 'topic-b']);
    expect(first[0].events.map(event => event.at)).toEqual([DAY1, DAY2, DATE]);
  });

  it('sorts sectors even when insertion order differs', () => {
    const lanes = groupRotationLanes([
      makeEvent(DAY1, 'zebra', ['Z'], 'segment_start', false, 'first_observation', HIST_SNAPSHOT),
      makeEvent(DAY1, 'alpha', ['A'], 'segment_start', false, 'first_observation', HIST_SNAPSHOT),
    ]);
    expect(lanes.map(lane => lane.sector_id)).toEqual(['alpha', 'zebra']);
  });
});

describe('RotationTimelineView', () => {
  function render(
    data: RotationData | null,
    selected: RotationNodeSelection | null = null,
  ) {
    return renderToStaticMarkup(createElement(RotationTimelineView, {
      data,
      days: 30,
      onDaysChange: () => {},
      selected,
      onSelectNode: () => {},
      onCloseEvidence: () => {},
      evidencePanel: selected ? createElement('div', null, 'EVIDENCE-PANEL-MARKER') : null,
    }));
  }

  it('renders title, window copy, lanes and all five change labels', () => {
    const markup = render(makeData());
    expect(markup).toContain('题材轮动时间轴');
    expect(markup).toContain('id="rotation-title"');
    expect(markup).toContain('2026-09-04–2026-09-08');
    expect(markup).toContain('按已发布版本呈现，历史不以当前成分回填');
    expect(markup).toContain('topic-a');
    expect(markup).toContain('topic-b');
    expect(markup).toContain(DAY1);
    expect(markup).toContain(DATE);
    expect(markup).toContain('新观察段');
    expect(markup).toContain('核心未变');
    expect(markup).toContain('核心扩大');
    expect(markup).toContain('核心缩小');
    expect(markup).toContain('核心重组');
    expect(markup).toContain('近 30 个交易日');
  });

  it('renders break-segment markers and Chinese reasons without causal wording', () => {
    const markup = render(makeData());
    expect(markup).toContain('不可比 · 新观察段');
    expect(markup).toContain('首次观察');
    expect(markup).toContain('无核心候选');
    for (const forbidden of ['接替', '接力', '取代', '替代', '易主', '领涨']) {
      expect(markup).not.toContain(forbidden);
    }
    expect(markup).not.toContain('Invalid Date');
  });

  it('renders the core security button ledger pinned to each serving snapshot', () => {
    const markup = render(makeData());
    expect(markup).toContain('data-security="A"');
    expect(markup).toContain(`data-snapshot="${HIST_SNAPSHOT}"`);
    expect(markup).toContain(`data-snapshot="${SNAPSHOT}"`);
    expect(markup).toContain(`aria-label="查看 A 于 ${DAY1} 的角色证据"`);
    expect(markup).toContain('aria-pressed="false"');
    // Empty core node offers no clickable core.
    expect(markup).toContain('无核心候选');
    const emptyLane = markup.split('rotation-node').find(block => block.includes('无核心候选')) ?? '';
    expect(emptyLane).not.toContain('<button');
  });

  it('exposes structural markers: labelled section, ordered lanes, timestamps and window selector', () => {
    const markup = render(makeData());
    expect(markup).toContain('<section class="leaders-section rotation-section" aria-labelledby="rotation-title"');
    expect(markup).toContain('<ol class="rotation-lane">');
    // React 19 server rendering emits the camelCase dateTime property name.
    expect(markup).toContain('<time dateTime="2026-09-04">2026-09-04</time>');
    expect(markup).toContain('aria-label="轮动窗口交易日数"');
    // Unselected core buttons stay collapsed but still declare the region
    // they would control.
    expect(markup).toContain('aria-expanded="false"');
    expect(markup).toContain('aria-controls="rotation-evidence-2026-09-04-topic-a-A"');
  });

  it('marks only the selected node button pressed and shows the evidence heading', () => {
    const selected: RotationNodeSelection = {
      snapshotId: SNAPSHOT,
      at: DATE,
      sectorId: 'topic-a',
      securityId: 'B',
    };
    const markup = render(makeData(), selected);
    expect(markup).toContain('aria-pressed="true"');
    expect(markup).toContain('B · 2026-09-08 角色证据（原始发布快照 00000000…）');
    expect(markup).toContain('返回时间轴');
    expect(markup).toContain('EVIDENCE-PANEL-MARKER');
    const pressedBlocks = markup.split('<li').filter(block => block.includes('aria-pressed="true"'));
    expect(pressedBlocks).toHaveLength(1);
    expect(pressedBlocks[0]).toContain('data-security="B"');
    // aria-expanded/aria-controls pair with the live evidence region whose
    // id derives from the same four-tuple.
    expect(pressedBlocks[0]).toContain('aria-expanded="true"');
    const regionId = 'rotation-evidence-2026-09-08-topic-a-B';
    expect(pressedBlocks[0]).toContain(`aria-controls="${regionId}"`);
    expect(markup).toContain(`id="${regionId}"`);
    expect(markup).toContain('role="region"');
    expect(markup).toContain('aria-live="polite"');
    expect(markup).toContain('aria-label="B 2026-09-08 角色证据"');
    const expandedCount = (markup.match(/aria-expanded="true"/g) ?? []).length;
    expect(expandedCount).toBe(1);
  });

  it('renders the no-publication empty state for null data', () => {
    const markup = render(null);
    expect(markup).toContain('当前快照暂无可发布的题材轮动数据。');
    expect(markup).not.toContain('rotation-lane');
  });

  it('renders the empty-window state for zero events', () => {
    const markup = render(makeData([]));
    expect(markup).toContain('窗口内没有可展示的轮动记录。');
    expect(markup).toContain('2026-09-04–2026-09-08');
    expect(markup).not.toContain('rotation-node');
  });
});

describe('SectionLoadError', () => {
  it('renders a section-scoped reload alert without private-workbench copy', () => {
    const markup = renderToStaticMarkup(createElement(SectionLoadError, { name: '题材轮动时间轴' }));
    expect(markup).toContain('role="alert"');
    expect(markup).toContain('题材轮动时间轴加载失败，请检查本机服务后重试。');
    expect(markup).toContain('重新加载并重试');
    expect(markup).toContain('class="notice error"');
    expect(markup).not.toContain('私人');
  });
});

describe('rotation request path allowlist', () => {
  it.each([
    '/api/topics/rotation-evil',
    '/api/topics/other',
    '//api/topics/rotation',
  ])('rejects non-whitelisted path %j without dispatching fetch', async path => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    await expect(request(path)).rejects.toMatchObject({
      status: 400,
      message: '仅允许同源只读接口。',
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
