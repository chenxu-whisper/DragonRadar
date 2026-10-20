import { lazy, Suspense, useEffect, useMemo, useState, type ReactNode } from 'react';
import { ResourceStatus } from '../../components/Common';
import { useResource } from '../../hooks/useResource';
import { rotationPath, validateRotation } from './api';
import { reasonLabel, rotationChangeLabel } from './labels';
import type { RotationData, RotationEvent } from './types';

// Evidence drill-down stays in a nested lazy chunk: selecting a core node
// must not pull the six-role evidence panel into the rotation entry path.
const EvidencePanel = lazy(() =>
  import('./EvidencePanel').then(module => ({ default: module.EvidencePanel })));

const ROTATION_DAY_OPTIONS = [10, 30, 60, 120];

export interface RotationNodeSelection {
  snapshotId: string;
  at: string;
  sectorId: string;
  securityId: string;
}

export interface RotationLane {
  sector_id: string;
  events: RotationEvent[];
}

// Events arrive globally sorted by (at, sector); keep lane order stable by
// insertion and return sectors in deterministic lexicographic order.
export function groupRotationLanes(events: readonly RotationEvent[]): RotationLane[] {
  const bySector = new Map<string, RotationEvent[]>();
  for (const event of events) {
    const lane = bySector.get(event.sector_id);
    if (lane) lane.push(event);
    else bySector.set(event.sector_id, [event]);
  }
  return [...bySector.keys()].sort().map(sectorId => ({
    sector_id: sectorId,
    events: bySector.get(sectorId) ?? [],
  }));
}

export interface RotationTimelineViewProps {
  data: RotationData | null;
  days: number;
  onDaysChange: (days: number) => void;
  selected: RotationNodeSelection | null;
  onSelectNode: (node: RotationNodeSelection) => void;
  onCloseEvidence: () => void;
  evidencePanel?: ReactNode;
}

export function RotationTimelineView({
  data,
  days,
  onDaysChange,
  selected,
  onSelectNode,
  onCloseEvidence,
  evidencePanel,
}: RotationTimelineViewProps) {
  const pressed = (event: RotationEvent, securityId: string): boolean =>
    selected !== null
    && selected.snapshotId === event.snapshot_id
    && selected.at === event.at
    && selected.sectorId === event.sector_id
    && selected.securityId === securityId;

  return <section className="leaders-section rotation-section" aria-labelledby="rotation-title">
    <div className="section-heading rotation-heading">
      <h2 id="rotation-title">题材轮动时间轴</h2>
      <label className="rotation-days">窗口
        <select
          aria-label="轮动窗口交易日数"
          value={days}
          onChange={event => onDaysChange(Number(event.target.value))}
        >
          {ROTATION_DAY_OPTIONS.map(value =>
            <option key={value} value={value}>近 {value} 个交易日</option>)}
        </select>
      </label>
    </div>
    {data === null
      ? <p className="empty rotation-empty">当前快照暂无可发布的题材轮动数据。</p>
      : <>
        <p className="muted rotation-meta">
          窗口 {data.window.start}–{data.window.end} · 按已发布版本呈现，历史不以当前成分回填
        </p>
        {data.events.length === 0
          ? <p className="empty rotation-empty">窗口内没有可展示的轮动记录。</p>
          : groupRotationLanes(data.events).map(lane =>
            <div className="rotation-lane-block" key={lane.sector_id}>
              <h3 className="rotation-sector">{lane.sector_id}</h3>
              <ol className="rotation-lane">
                {lane.events.map(event =>
                  <li
                    key={`${event.at}:${event.sector_id}`}
                    className={event.comparable ? 'rotation-node' : 'rotation-node rotation-break'}
                  >
                    <div className="rotation-node-meta">
                      <time dateTime={event.at}>{event.at}</time>
                      <span className="rotation-change">{rotationChangeLabel(event.change)}</span>
                      <span className={event.comparable
                        ? 'rotation-comparable'
                        : 'rotation-comparable rotation-break-badge'}>
                        {event.comparable
                          ? '可比'
                          : event.change === 'segment_start' ? '不可比 · 新观察段' : '不可比'}
                      </span>
                    </div>
                    {event.reason !== null
                      && <p className="rotation-reason">{reasonLabel(event.reason)}</p>}
                    <div className="rotation-cores">
                      {event.core_ids.length === 0
                        ? <span className="rotation-no-core">无核心候选</span>
                        : event.core_ids.map(securityId =>
                          <button
                            type="button"
                            key={securityId}
                            className="rotation-core"
                            data-security={securityId}
                            data-snapshot={event.snapshot_id}
                            aria-pressed={pressed(event, securityId)}
                            aria-label={`查看 ${securityId} 于 ${event.at} 的角色证据`}
                            onClick={() => onSelectNode({
                              snapshotId: event.snapshot_id,
                              at: event.at,
                              sectorId: event.sector_id,
                              securityId,
                            })}
                          >
                            {securityId}
                          </button>)}
                    </div>
                  </li>)}
              </ol>
            </div>)}
        {selected && <div
          className="rotation-evidence"
          key={`${selected.snapshotId}:${selected.at}:${selected.sectorId}:${selected.securityId}`}
        >
          <div className="rotation-evidence-heading">
            <h3 className="rotation-evidence-title">
              {selected.securityId} · {selected.at} 角色证据（原始发布快照 {selected.snapshotId.slice(0, 8)}…）
            </h3>
            <button type="button" className="rotation-back" onClick={onCloseEvidence}>返回时间轴</button>
          </div>
          {evidencePanel}
        </div>}
      </>}
  </section>;
}

interface RotationTimelineProps {
  date: string;
  snapshot: string;
  sector?: string;
  refreshToken: number;
}

export default function RotationTimeline({ date, snapshot, sector, refreshToken }: RotationTimelineProps) {
  const [days, setDays] = useState(30);
  const [selected, setSelected] = useState<RotationNodeSelection | null>(null);
  const resource = useResource<unknown>(
    snapshot && date ? rotationPath({ date, snapshot, sector, days }) : null,
    snapshot,
    refreshToken,
  );
  const decoded = useMemo(() => {
    if (resource.data === undefined) return {};
    try {
      return {
        value: validateRotation(resource.data, { snapshot, date, sector }, days),
      };
    } catch (error) {
      return { error: error instanceof Error ? error.message : '题材轮动响应格式无效' };
    }
  }, [resource.data, snapshot, date, sector, days]);

  // Anchor or window changes invalidate the selected historical node; its
  // serving snapshot id must never be replaced by the current publication.
  useEffect(() => { setSelected(null); }, [snapshot, date, sector, days]);

  return <>
    <ResourceStatus resource={resource} name="题材轮动时间轴" />
    {decoded.error && <p className="notice error" role="alert">{decoded.error}</p>}
    {decoded.value && <RotationTimelineView
      data={decoded.value.data}
      days={days}
      onDaysChange={setDays}
      selected={selected}
      onSelectNode={setSelected}
      onCloseEvidence={() => setSelected(null)}
      evidencePanel={selected
        ? <Suspense fallback={<p role="status" className="status">正在加载角色证据…</p>}>
          <EvidencePanel
            snapshot={selected.snapshotId}
            date={selected.at}
            sector={selected.sectorId}
            security={selected.securityId}
            refreshToken={refreshToken}
          />
        </Suspense>
        : null}
    />}
  </>;
}
