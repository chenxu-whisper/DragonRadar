export interface PublicationEligibility {
  eligible: boolean;
  reason: string | null;
  revision?: number;
  expected_review_id?: string;
  expected_content_hash?: string;
  snapshot_id?: string;
  date?: string;
  kind?: 'daily_ai' | 'summary';
}
export interface PublicationCommand {
  revision: number;
  expected_review_id: string;
  expected_content_hash: string;
  snapshot_id: string;
  date: string;
  idempotency_key: string;
  publication_consent_reference: string;
  output_entitlement_reference: string;
  attestation_reference: string;
  expected_archive_version?: number;
}
export interface PublicationReceipt {
  id: string;
  version: number;
  content_hash: string;
  kind: 'daily_ai' | 'summary';
}
export class PublicationError extends Error {
  constructor(readonly status: number) { super('Publication command did not complete'); }
  get uncertain() { return this.status === 0 || this.status >= 500; }
}
const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
const uuid = (value: unknown): value is string => typeof value === 'string'
  && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);
const sha = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);

async function request(path: string, signal: AbortSignal, body?: Record<string, unknown>): Promise<unknown> {
  if (signal.aborted) throw new DOMException('Cancelled', 'AbortError');
  try {
    const response = await fetch(path, {
      method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
      signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
      headers: body ? {
        Accept: 'application/json', 'Content-Type': 'application/json',
        'X-CSRF-Token': document.cookie.split(';').map(item => item.trim())
          .find(item => item.startsWith('__Host-dragon-private-csrf='))?.slice('__Host-dragon-private-csrf='.length) || '',
      } : { Accept: 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!response.ok) {
      if (response.status === 401) window.dispatchEvent(new Event('private-session-lock'));
      if (response.status === 403) window.dispatchEvent(new Event('private-source-revoked'));
      throw new PublicationError(response.status);
    }
    const value: unknown = await response.json();
    if (signal.aborted) throw new DOMException('Cancelled', 'AbortError');
    return value;
  } catch (error) {
    if (signal.aborted) throw new DOMException('Cancelled', 'AbortError');
    if (error instanceof PublicationError) throw error;
    throw new PublicationError(0);
  }
}

export async function publicationEligibility(id: string, reference: string, signal: AbortSignal) {
  if (!uuid(id) || !reference) throw new PublicationError(400);
  const query = new URLSearchParams({ output_entitlement_reference: reference });
  const value = await request(`/api/ai/task/${id}/publication-eligibility?${query}`, signal);
  if (!object(value) || typeof value.eligible !== 'boolean') throw new PublicationError(502);
  if (value.eligible && (!Number.isSafeInteger(value.revision) || Number(value.revision) < 1
    || !uuid(value.expected_review_id) || !uuid(value.snapshot_id) || !sha(value.expected_content_hash)
    || typeof value.date !== 'string' || !['daily_ai', 'summary'].includes(String(value.kind)))) {
    throw new PublicationError(502);
  }
  return value as unknown as PublicationEligibility;
}

export async function publishRevision(id: string, kind: 'daily_ai' | 'summary', command: PublicationCommand, signal: AbortSignal) {
  if (!uuid(id)) throw new PublicationError(400);
  const value = await request(`/api/ai/task/${id}/publish-${kind === 'daily_ai' ? 'daily' : 'summary'}`,
    signal, command as unknown as Record<string, unknown>);
  if (!object(value)) throw new PublicationError(502);
  const receipt = {
    id: kind === 'daily_ai' ? value.archive_id : value.summary_id,
    version: kind === 'daily_ai' ? value.archive_version : value.summary_revision,
    content_hash: value.content_hash,
    kind,
  };
  if (!uuid(receipt.id) || !Number.isSafeInteger(receipt.version) || Number(receipt.version) < 1 || !sha(receipt.content_hash)) {
    throw new PublicationError(502);
  }
  return receipt as PublicationReceipt;
}

export async function withdrawRevision(
  id: string, kind: 'daily_ai' | 'summary', receipt: PublicationReceipt, key: string, signal: AbortSignal,
) {
  if (!uuid(id) || !uuid(receipt.id)) throw new PublicationError(400);
  const value = await request(`/api/ai/task/${id}/published-${kind === 'daily_ai' ? 'daily' : 'summary'}/${receipt.id}/revoke`,
    signal, {
      ...(kind === 'daily_ai'
        ? { expected_archive_version: receipt.version }
        : { expected_summary_revision: receipt.version }),
      idempotency_key: key, reason_code: 'withdrawn_by_owner',
    });
  if (!object(value) || value.status !== 'unavailable') throw new PublicationError(502);
}
