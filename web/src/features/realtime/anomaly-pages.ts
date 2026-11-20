import { ApiError } from '../../api';
import { anomalyPath, validateAnomalyResponse } from './client';
import type { AnomalyEvent, AnomalyResponse, AnomalySelection } from './types';

export interface AnomalyPageState {
  data?: AnomalyResponse;
  error?: ApiError;
  loading: boolean;
  hasMore: boolean;
}

export class AnomalyPages {
  state: AnomalyPageState = { loading: false, hasMore: false };
  private selection: AnomalySelection | null = null;
  private key: string | null = null;
  private generation = 0;
  private controller?: AbortController;
  private cursors = new Set<string>();
  constructor(
    readonly fetchPage: (path: string, options: { signal: AbortSignal }) => Promise<unknown>,
    readonly changed: () => void = () => {},
  ) {}
  reset(key: string | null, selection: AnomalySelection | null): void {
    this.controller?.abort();
    this.generation += 1;
    this.key = key;
    this.selection = selection ? { ...selection, cursor: undefined } : null;
    this.cursors.clear();
    this.state = { loading: false, hasMore: Boolean(key && selection) };
    this.changed();
  }
  async load(): Promise<void> {
    if (!this.key || !this.selection || this.state.loading || !this.state.hasMore) return;
    const generation = this.generation;
    const previous = this.state.data;
    const cursor = previous?.data?.next_cursor ?? undefined;
    const selected = {
      ...this.selection,
      asOf: this.selection.asOf ?? previous?.as_of,
      cursor,
    };
    const controller = new AbortController();
    this.controller = controller;
    this.state = { ...this.state, loading: true, error: undefined };
    this.changed();
    try {
      const raw = await this.fetchPage(anomalyPath(selected), { signal: controller.signal });
      if (generation !== this.generation || controller.signal.aborted) return;
      let response: AnomalyResponse;
      try {
        response = validateAnomalyResponse(raw, selected);
        const before = previous?.data?.events ?? [];
        const added = response.data?.events ?? [];
        const ids = new Set(before.map(event => event.event_id));
        let last = before.at(-1);
        for (const event of added) {
          if (ids.has(event.event_id) || (last && !descends(last, event))) {
            throw new Error('Invalid anomaly sequence');
          }
          ids.add(event.event_id);
          last = event;
        }
        const next = response.data?.next_cursor;
        if (next && (next === cursor || this.cursors.has(next) || !added.length)) {
          throw new Error('Invalid anomaly cursor');
        }
        if (next) this.cursors.add(next);
        if (response.data) response = {
          ...response, data: { ...response.data, events: [...before, ...added] },
        };
      } catch {
        throw new ApiError(502, '异动分页响应无效，已清除该链。', 'malformed_response');
      }
      this.state = { data: response, loading: false, hasMore: Boolean(response.data?.next_cursor) };
    } catch (error) {
      if (generation !== this.generation || controller.signal.aborted) return;
      const failure = error instanceof ApiError ? error : new ApiError(0);
      const retain = failure.status === 0 || failure.status === 408
        || (failure.status >= 500 && failure.code !== 'malformed_response');
      this.state = {
        data: retain ? previous : undefined, error: failure, loading: false,
        hasMore: retain,
      };
    } finally {
      if (generation === this.generation) {
        this.controller = undefined;
        this.changed();
      }
    }
  }
}

function micros(value: string): bigint {
  const fraction = value.match(/\.(\d+)(?:Z|[+-]\d\d:\d\d)$/)?.[1] ?? '';
  return BigInt(Date.parse(value)) * 1000n + BigInt(fraction.padEnd(6, '0').slice(3, 6));
}
function descends(before: AnomalyEvent, after: AnomalyEvent): boolean {
  const a = micros(before.available_at);
  const b = micros(after.available_at);
  return b < a || (b === a && after.event_id < before.event_id);
}
