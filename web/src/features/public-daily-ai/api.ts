export interface ArchiveMetadata {
  archive_id: string;
  archive_version: number;
  archive_published_at: string;
  publication_sequence: number;
  content_hash: string;
  attestation_reference: string;
}
export interface PublicCitation {
  evidence_id: string;
  source_identity: string;
  locator: { snapshot_id: string; position: string };
  fragment: string | number | boolean;
}
export interface PublicPayload {
  facts: { text: string; evidence_id: string; evidence_position: string; value: unknown }[];
  inferences: { text: string; evidence_ids: string[] }[];
  missing_evidence: { text: string; reason?: string }[];
  citations: PublicCitation[];
}
export interface PublicArchive extends ArchiveMetadata {
  schema: 'dragon-radar.public-daily-ai/v1';
  payload: PublicPayload;
}
export interface DailyAI {
  snapshot_id: string;
  trade_date: string;
  as_of: string;
  base_published_at: string;
  archive_published_at: string | null;
  archive_version: number | null;
  quality_flags: string[];
  data: PublicArchive | null;
}
export interface Discovery {
  snapshot_id: string;
  trade_date: string;
  base_published_at: string;
  publication_sequence_watermark: number;
  items: ArchiveMetadata[];
  total: number;
  next_cursor: string | null;
}
const uuid = (value: unknown): value is string =>
  typeof value === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);
const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
const exact = (value: Record<string, unknown>, keys: string[]) =>
  Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const aware = (value: unknown): value is string =>
  typeof value === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
const integer = (value: unknown, min = 0): value is number =>
  Number.isSafeInteger(value) && Number(value) >= min;
const metaKeys = ['archive_id', 'archive_version', 'archive_published_at', 'publication_sequence', 'content_hash', 'attestation_reference'];
const metadata = (value: unknown) => object(value) && uuid(value.archive_id)
  && integer(value.archive_version, 1) && integer(value.publication_sequence, 1)
  && aware(value.archive_published_at) && typeof value.content_hash === 'string'
  && /^[0-9a-f]{64}$/.test(value.content_hash)
  && typeof value.attestation_reference === 'string' && value.attestation_reference.length > 0;
const invalid = (): never => { throw new Error('Invalid public archive response'); };

function payload(value: unknown, pub: string): value is PublicPayload {
  if (!object(value) || !exact(value, ['facts', 'inferences', 'missing_evidence', 'citations'])) return false;
  const fields: Record<string, string[]> = {
    facts: ['text', 'evidence_id', 'evidence_position', 'value', 'unit', 'precision', 'recomputation'],
    inferences: ['text', 'claim', 'confidence', 'evidence_ids'],
    missing_evidence: ['text', 'reason'],
    citations: ['evidence_id', 'source_identity', 'locator', 'fragment'],
  };
  for (const [key, allowed] of Object.entries(fields)) {
    const items = value[key];
    if (!Array.isArray(items) || items.length > 1000) return false;
    for (const item of items) {
      if (!object(item) || Object.keys(item).some(field => !allowed.includes(field))) return false;
      if (key !== 'citations' && (typeof item.text !== 'string' || !item.text.trim())) return false;
      if (key === 'citations' && (!exact(item, allowed)
        || typeof item.evidence_id !== 'string' || typeof item.source_identity !== 'string'
        || !object(item.locator) || !exact(item.locator, ['snapshot_id', 'position'])
        || item.locator.snapshot_id !== pub || typeof item.locator.position !== 'string'
        || !/^\/input\/(?:article|market_overview)\//.test(item.locator.position)
        || !['string', 'number', 'boolean'].includes(typeof item.fragment))) return false;
    }
  }
  return new TextEncoder().encode(JSON.stringify(value)).length <= 32768;
}

export function validateArchive(value: unknown, pub: string, date: string): DailyAI {
  const envelopeKeys = ['trade_date', 'as_of', 'snapshot_id', 'source', 'source_version', 'coverage',
    'quality_flags', 'rule_version', 'basis', 'data', 'base_published_at', 'archive_published_at', 'archive_version'];
  if (!object(value) || !exact(value, envelopeKeys) || value.snapshot_id !== pub || value.trade_date !== date
    || !aware(value.as_of) || !aware(value.base_published_at) || !Array.isArray(value.quality_flags)) invalid();
  const result = value as unknown as DailyAI;
  if (result.data === null) {
    if (result.archive_version !== null || result.archive_published_at !== null) invalid();
  } else if (!object(result.data) || !exact(result.data as unknown as Record<string, unknown>, [...metaKeys, 'schema', 'payload'])
    || !metadata(result.data) || result.data.schema !== 'dragon-radar.public-daily-ai/v1'
    || !payload(result.data.payload, pub) || result.archive_version !== result.data.archive_version
    || result.archive_published_at !== result.data.archive_published_at) invalid();
  return result;
}

export function validateDiscovery(value: unknown, pub: string, date: string): Discovery {
  if (!object(value) || !exact(value, ['snapshot_id', 'trade_date', 'base_published_at',
    'publication_sequence_watermark', 'items', 'total', 'next_cursor'])
    || value.snapshot_id !== pub || value.trade_date !== date || !aware(value.base_published_at)
    || !integer(value.publication_sequence_watermark) || !integer(value.total)
    || !(value.next_cursor === null || typeof value.next_cursor === 'string')
    || !Array.isArray(value.items) || value.items.length > 100
    || !value.items.every(item => object(item) && exact(item, metaKeys) && metadata(item))) invalid();
  return value as unknown as Discovery;
}

export class PublicArchiveGeneration {
  private generation = 0;
  private resource = '';
  begin(resource: string) { this.resource = resource; return ++this.generation; }
  clear() { this.resource = ''; this.generation++; }
  accept(generation: number, resource: string) {
    return this.generation === generation && this.resource === resource;
  }
}

export class ArchiveReadError extends Error {
  constructor(readonly status: number) { super('Public archive unavailable'); }
}

async function request(path: string, signal: AbortSignal): Promise<unknown> {
  const response = await fetch(path, {
    signal, method: 'GET', credentials: 'omit', cache: 'no-store',
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new ArchiveReadError(response.status);
  const data: unknown = await response.json();
  if (signal.aborted) throw new DOMException('Request cancelled', 'AbortError');
  return data;
}

export async function readArchive(pub: string, date: string, signal: AbortSignal, id?: string) {
  if (!uuid(pub) || !/^\d{4}-\d{2}-\d{2}$/.test(date) || (id !== undefined && !uuid(id))) invalid();
  const query = new URLSearchParams({ date, snapshot_id: pub });
  if (id) query.set('archive_id', id);
  const result = validateArchive(await request(`/api/market/daily-ai?${query}`, signal), pub, date);
  if (id && result.data?.archive_id !== id) invalid();
  return result;
}

export async function readArchives(pub: string, date: string, signal: AbortSignal, cursor?: string) {
  if (!uuid(pub) || !/^\d{4}-\d{2}-\d{2}$/.test(date)) invalid();
  const query = new URLSearchParams({ date, snapshot_id: pub, limit: '50' });
  if (cursor) query.set('cursor', cursor);
  return validateDiscovery(await request(`/api/market/daily-ai/archives?${query}`, signal), pub, date);
}

export function resolvePublicOverviewCitation(overview: unknown, citation: PublicCitation): string | number | boolean {
  if (!citation.locator.position.startsWith('/input/market_overview/')) invalid();
  let current: unknown = overview;
  for (const raw of citation.locator.position.slice('/input/market_overview/'.length).split('/')) {
    const key = raw.replace(/~1/g, '/').replace(/~0/g, '~');
    if (Array.isArray(current) && /^(0|[1-9]\d*)$/.test(key)) current = current[Number(key)];
    else if (object(current) && Object.hasOwn(current, key)) current = current[key];
    else invalid();
  }
  if (current !== citation.fragment || !['string', 'number', 'boolean'].includes(typeof current)) invalid();
  return current as string | number | boolean;
}

export async function readCitationSource(
  pub: string, date: string, archiveId: string, citation: PublicCitation, signal: AbortSignal,
) {
  if (citation.locator.snapshot_id !== pub || !citation.locator.position.startsWith('/input/market_overview/')) invalid();
  const query = new URLSearchParams({ date, snapshot_id: pub });
  const [archive, overview] = await Promise.all([
    readArchive(pub, date, signal, archiveId),
    request(`/api/market/overview?${query}`, signal),
  ]);
  const matches = archive.data?.payload.citations.filter(item => item.evidence_id === citation.evidence_id) || [];
  if (matches.length !== 1 || JSON.stringify(matches[0]) !== JSON.stringify(citation)
    || !object(overview) || overview.snapshot_id !== pub || overview.trade_date !== date
    || !aware(overview.as_of) || !object(overview.data)) invalid();
  return { fragment: resolvePublicOverviewCitation((overview as Record<string, unknown>).data, citation),
    source_identity: citation.source_identity, snapshot_id: pub, position: citation.locator.position };
}
