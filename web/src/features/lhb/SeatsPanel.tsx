import { useEffect, useMemo, useRef, useState } from 'react';
import { ApiError } from '../../api';
import { Pager, ResourceStatus, TableScroll } from '../../components/Common';
import { timestamp } from '../../format';
import type { Resource } from '../../hooks/useResource';
import {
  disclosurePath,
  formatLhbDecimal,
  seatHistoryPath,
  useLhbResource,
  validateDisclosureList,
  validateSeatHistory,
} from './client';
import type {
  AggregatedSeat,
  DisclosureItem,
  DisclosureListResponse,
  DisclosureSeat,
  SeatHistoryResponse,
  SeatCompleteness,
  SeatContribution,
} from './types';

interface Props {
  date: string;
  securityId: string;
  asOf: string;
  refreshToken?: number;
}

const PAGE_SIZE = 20;
const HISTORY_DAYS = 30;
const SYNTHETIC_BADGE = '合成演示数据 · 未获真实行情授权';

const completenessNames: Record<SeatCompleteness, string> = {
  complete: '完整',
  buy_unknown: '买入未知',
  sell_unknown: '卖出未知',
  both_unknown: '买卖均未知',
};

const sideNames: Record<string, string> = {
  buy: '买入',
  sell: '卖出',
  net: '净额',
};

function useValidated<T>(
  resource: Resource<unknown>,
  validate: (value: unknown) => T,
  name: string,
): Resource<T> {
  return useMemo(() => {
    if (resource.data === undefined) return { ...resource, data: undefined };
    try {
      return { ...resource, data: validate(resource.data) };
    } catch {
      return {
        ...resource,
        data: undefined,
        error: new ApiError(502, `${name}响应格式无效，已丢弃该响应。`, 'malformed_response'),
      };
    }
  }, [resource, validate, name]);
}

function amount(value: string | null): string {
  return formatLhbDecimal(value);
}

function CompletenessToken({ value }: { value: SeatCompleteness }) {
  return <span className={`status-token ${value === 'complete' ? 'state-complete' : 'state-partial'}`}>
    {completenessNames[value]}
  </span>;
}

function ForbiddenNotice({ onRetry }: { onRetry: () => void }) {
  return <div className="notice error" role="alert">
    <span>来源访问被拒绝：当前席位来源未在本研究环境开放，面板不会展示受保护内容。</span>
    <button type="button" onClick={onRetry}>重新检查来源</button>
  </div>;
}

function SyntheticBadge() {
  return <span className="status-token state-missing" data-testid="lhb-synthetic-badge">
    {SYNTHETIC_BADGE}
  </span>;
}

function SeatRow({
  item,
  seat,
  onHistory,
}: {
  item: DisclosureItem;
  seat: DisclosureSeat;
  onHistory: (seat: DisclosureSeat, trigger: HTMLElement) => void;
}) {
  return <tr key={`${item.source_id}\u0000${seat.source_row_id}`}>
    <th data-label="席位">
      <strong>{seat.source_seat_name}</strong>
      <small>{seat.seat_id} · {seat.mapping_version} · 行 {seat.source_row_id}</small>
    </th>
    <td data-label="方向">{seat.side ? sideNames[seat.side] : '未知'}</td>
    <td data-label="序号">{seat.rank ?? '未知'}</td>
    <td data-label="买入">{amount(seat.buy_amount)}</td>
    <td data-label="卖出">{amount(seat.sell_amount)}</td>
    <td data-label="净额">{amount(seat.net_amount)}</td>
    <td data-label="单位">{seat.currency} / {seat.unit}</td>
    <td data-label="完整度"><CompletenessToken value={seat.completeness} /></td>
    <td data-label="席位历史">
      <button
        type="button"
        className="seat-history-button"
        aria-label={`查看席位 ${seat.seat_id} 近 ${HISTORY_DAYS} 日历史`}
        onClick={event => onHistory(seat, event.currentTarget)}
      >近 {HISTORY_DAYS} 日历史</button>
    </td>
  </tr>;
}

function ContributionTable({ contributions }: { contributions: SeatContribution[] }) {
  return <TableScroll label="席位历史组成披露横向滚动">
    <table className="responsive-record-table" aria-label="席位历史组成披露">
      <thead><tr>
        <th>披露日期</th><th>证券</th><th>修订</th><th>原因</th>
        <th>来源行</th><th>席位名</th><th>映射</th><th>权限状态（接口原值）</th>
      </tr></thead>
      <tbody>{contributions.map(contribution =>
        <tr key={`${contribution.disclosure_id}-${contribution.source_row_id}-${contribution.revision}`}>
          <th data-label="披露日期">{contribution.disclosure_date}</th>
          <td data-label="证券">{contribution.security_id}</td>
          <td data-label="修订">{contribution.revision}</td>
          <td data-label="原因">{contribution.reason}</td>
          <td data-label="来源行">{contribution.source_row_id}</td>
          <td data-label="席位名">{contribution.source_seat_name}</td>
          <td data-label="映射">{contribution.mapping_version}</td>
          <td data-label="权限状态（接口原值）">
            <span className="status-token state-partial">{contribution.permission_state}</span>
          </td>
        </tr>)}</tbody>
    </table>
  </TableScroll>;
}

function AggregatedHistory({ aggregated }: { aggregated: AggregatedSeat }) {
  return <li>
    <div className="leader-state" aria-label={`席位 ${aggregated.seat_id} 聚合结果`}>
      <strong>{aggregated.source_seat_name}</strong>
      <small>{aggregated.seat_id} · {aggregated.source_id} / {aggregated.source_version}</small>
      <CompletenessToken value={aggregated.completeness} />
      <span>买入 {amount(aggregated.buy_amount)} · 卖出 {amount(aggregated.sell_amount)}
        {' '}· 净额 {amount(aggregated.net_amount)} {aggregated.currency} / {aggregated.unit}</span>
    </div>
    <ContributionTable contributions={aggregated.contributions} />
  </li>;
}

function SeatHistoryDialog({
  seatId,
  date,
  asOf,
  trigger,
  onClose,
}: {
  seatId: string;
  date: string;
  asOf: string;
  trigger: HTMLElement;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const selection = useMemo(
    () => ({ seatId, date, days: HISTORY_DAYS, asOf: asOf || undefined }),
    [seatId, date, asOf],
  );
  const path = useMemo(() => seatHistoryPath(selection), [selection]);
  const raw = useLhbResource<unknown>(path);
  const history = useValidated(
    raw,
    value => validateSeatHistory(value, selection),
    '席位历史',
  );

  useEffect(() => {
    dialogRef.current?.showModal();
    closeRef.current?.focus();
    return () => {
      dialogRef.current?.close();
      if (trigger.isConnected) trigger.focus();
    };
  }, [trigger]);

  const data = history.data?.data;
  const notFound = raw.error?.status === 404;
  const empty = history.data?.quality_flags.includes('no_data') && data?.total === 0;
  return <dialog
    ref={dialogRef}
    className="lhb-seat-history-dialog"
    aria-label={`席位 ${seatId} 近 ${HISTORY_DAYS} 日上榜历史`}
    onCancel={event => { event.preventDefault(); onClose(); }}
  >
    <div className="drawer-toolbar">
      <h3>席位 {seatId} · 近 {HISTORY_DAYS} 日上榜历史</h3>
      <button ref={closeRef} type="button" onClick={onClose} aria-label="关闭席位历史" title="关闭（退出键）">
        关闭
      </button>
    </div>
    <SyntheticBadge />
    {raw.error?.status === 403
      ? <ForbiddenNotice onRetry={raw.refresh} />
      : raw.error?.status !== 404 && <ResourceStatus resource={history} name="席位历史" />}
    {notFound && <p className="empty">该席位在近 {HISTORY_DAYS} 日内没有可显示的上榜历史。</p>}
    {history.data && data && !notFound && <>
      {data.identity_state === 'unknown'
        ? <p className="notice" role="status">未映射席位（占位身份，非真实来源）。</p>
        : <dl className="analysis-context" aria-label="席位身份">
          <div><dt>来源标识</dt><dd>{data.seat.source_id}</dd></div>
          <div><dt>席位名称</dt><dd>{data.seat.source_seat_name}</dd></div>
          <div><dt>映射版本</dt><dd>{data.seat.mapping_version}</dd></div>
        </dl>}
      {empty
        ? <p className="empty">该席位在当前研究时点之前没有可显示的上榜历史。</p>
        : <ol className="lhb-history-list">
          {data.items.map((item, index) => <AggregatedHistory
            key={`${item.source_id}\u0000${item.seat_id}\u0000${item.contributions[0]?.disclosure_date ?? index}`}
            aggregated={item}
          />)}
        </ol>}
    </>}
  </dialog>;
}

export function SeatsPanel({ date, securityId, asOf, refreshToken = 0 }: Props) {
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(PAGE_SIZE);
  const [active, setActive] = useState<{
    seatId: string;
    trigger: HTMLElement;
  } | null>(null);

  const selection = useMemo(
    () => ({ date, securityId, page, size, asOf: asOf || undefined }),
    [date, securityId, page, size, asOf],
  );
  const ready = Boolean(date && securityId && asOf);
  const path = useMemo(
    () => ready ? disclosurePath(selection) : null,
    [ready, selection],
  );
  const raw = useLhbResource<unknown>(path, refreshToken);
  const list = useValidated(
    raw,
    value => validateDisclosureList(value, selection),
    '龙虎榜披露',
  );

  if (!ready) return null;
  const data = list.data?.data;
  const envelope = list.data as DisclosureListResponse | undefined;
  const coverage = envelope?.coverage;
  const hasUnknownSeats = Boolean(coverage && coverage.unknown_seats > 0)
    || Boolean(data?.items.some(item => item.seats.some(seat => seat.completeness !== 'complete')));
  const emptyWindow = envelope?.quality_flags.includes('no_data') && data?.total === 0;
  const changePage = (nextPage: number, nextSize: number) => {
    setSize(nextSize);
    setPage(nextPage);
  };

  return <section className="securities lhb-seats-panel" aria-label="龙虎榜席位证据">
    <div className="section-heading">
      <div>
        <p className="eyebrow">龙虎榜 / EX08–EX09 只读证据</p>
        <h2>龙虎榜席位证据</h2>
      </div>
      <SyntheticBadge />
    </div>

    {raw.error?.status === 403
      ? <ForbiddenNotice onRetry={raw.refresh} />
      : <ResourceStatus resource={list} name="龙虎榜披露" />}

    {envelope && coverage && <div className="leader-state" aria-label="龙虎榜覆盖情况">
      {hasUnknownSeats
        ? <span className="status-token state-partial">部分未知</span>
        : <span className="status-token state-complete">完整</span>}
      <span>披露 {coverage.total_disclosures} · 已可见 {coverage.visible_disclosures}
        {' '}· 延迟 {coverage.late_disclosures} · 本页选取 {coverage.selected_disclosures}
      </span>
      <span>席位 {coverage.seat_count}（完整 {coverage.complete_seats} · 未知 {coverage.unknown_seats}）</span>
      <span>{envelope.source ?? '当页来源混合'} / {envelope.source_version ?? '版本混合'}</span>
    </div>}

    {emptyWindow && <p className="empty">
      当前研究时点没有可显示的龙虎榜披露；不会用其他日期或来源的数据补齐。
    </p>}

    {data && data.items.map(item => (
      <section
        className="leaders-section"
        key={`${item.source_id}\u0000${item.disclosure_id}-${item.revision}`}
        aria-label={`披露 ${item.disclosure_id}`}
      >
      <div className="section-heading">
        <h3>{item.reason}</h3>
        <span className="muted">修订 {item.revision} · {item.source_id} / {item.source_version}</span>
      </div>
      <dl className="analysis-context">
        <div><dt>来源事件时间</dt><dd><time dateTime={item.source_event_at}>{timestamp(item.source_event_at)}</time></dd></div>
        <div><dt>平台接收时间</dt><dd><time dateTime={item.received_at}>{timestamp(item.received_at)}</time></dd></div>
        <div><dt>可用时间</dt><dd><time dateTime={item.available_at}>{timestamp(item.available_at)}</time></dd></div>
      </dl>
      <TableScroll label="龙虎榜席位记录横向滚动">
        <table className="responsive-record-table" aria-label={`披露 ${item.disclosure_id} 席位记录`}>
          <thead><tr>
            <th>席位</th><th>方向</th><th>序号</th><th>买入</th><th>卖出</th>
            <th>净额</th><th>单位</th><th>完整度</th><th>席位历史</th>
          </tr></thead>
          <tbody>{item.seats.map(seat =>
            <SeatRow key={`${item.source_id}\u0000${seat.source_row_id}`} item={item} seat={seat} onHistory={(selected, trigger) =>
              setActive({ seatId: selected.seat_id, trigger })} />)}</tbody>
        </table>
      </TableScroll>
      </section>
    ))}

    {data && data.total > 0 && <Pager
      page={page}
      size={size}
      total={data.total}
      change={changePage}
    />}

    {active && <SeatHistoryDialog
      seatId={active.seatId}
      date={date}
      asOf={asOf}
      trigger={active.trigger}
      onClose={() => setActive(null)}
    />}
  </section>;
}
