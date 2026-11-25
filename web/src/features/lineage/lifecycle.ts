import type { AcceptedLineage, LineageSelection } from './types';
import { parseLineage, selectionKey } from './validation';

export interface LineageTicket {
  readonly generation: number;
  readonly selection: LineageSelection;
  readonly key: string;
}
export class LineageLifecycle {
  private generation = 0;
  private key: string | null = null;
  private accepted: AcceptedLineage | null = null;
  get current(): AcceptedLineage | null { return this.accepted; }
  begin(selection: LineageSelection): LineageTicket {
    const key = selectionKey(selection);
    if (key !== this.key) this.accepted = null;
    this.key = key;
    return Object.freeze({ generation: ++this.generation, key,
      selection: Object.freeze({ ...selection }) });
  }
  accept(ticket: LineageTicket, value: unknown, receivedAt: Date): boolean {
    if (ticket.generation !== this.generation || ticket.key !== this.key) return false;
    const data = parseLineage(value, ticket.selection);
    const milliseconds = receivedAt.getTime();
    if (!Number.isFinite(milliseconds) ||
      this.accepted && milliseconds < Date.parse(this.accepted.page_updated_at)) return false;
    this.accepted = Object.freeze({ selection_key: ticket.key, data,
      page_updated_at: receivedAt.toISOString() });
    return true;
  }
  deny(ticket: LineageTicket): void {
    if (ticket.generation === this.generation && ticket.key === this.key) this.reset();
  }
  reset(): void { ++this.generation; this.key = null; this.accepted = null; }
}
