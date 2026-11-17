interface JsonParseContext {
  source?: string;
}

type JsonReviver = (
  this: unknown,
  key: string,
  value: unknown,
  context: JsonParseContext,
) => unknown;

type StructuredJsonParse = (text: string, reviver?: JsonReviver) => unknown;

const DECIMAL_FIELDS = {
  quotes: new Set(['last', 'bid', 'ask', 'bid_size', 'ask_size', 'volume', 'amount']),
  minutes: new Set(['open', 'high', 'low', 'close', 'volume', 'amount']),
  anomalies: new Set(['value', 'threshold']),
  flow: new Set(['net_inflow']),
} as const;

export class RealtimeDataFormatError extends Error {
  constructor() {
    super('JSON.parse context.source is unsupported');
    this.name = 'RealtimeDataFormatError';
  }
}

function nativeParse(): StructuredJsonParse {
  return JSON.parse as StructuredJsonParse;
}

export function supportsJsonParseSource(parse: StructuredJsonParse = nativeParse()): boolean {
  let supported = false;
  try {
    parse('0', (_key, value, context) => {
      supported ||= context?.source === '0';
      return value;
    });
  } catch {
    return false;
  }
  return supported;
}

function decimalFields(path: string): ReadonlySet<string> {
  if (/^\/api\/realtime\/quotes(?:[?#]|$)/i.test(path)) return DECIMAL_FIELDS.quotes;
  if (/^\/api\/realtime\/minutes(?:[?#]|$)/i.test(path)) return DECIMAL_FIELDS.minutes;
  if (/^\/api\/realtime\/anomalies(?:[?#]|$)/i.test(path)) return DECIMAL_FIELDS.anomalies;
  if (/^\/api\/market\/sector-flow\/intraday(?:[?#]|$)/i.test(path)) {
    return DECIMAL_FIELDS.flow;
  }
  throw new RealtimeDataFormatError();
}

export function parseRealtimeJson(
  text: string,
  path: string,
  parse: StructuredJsonParse = nativeParse(),
): unknown {
  if (!supportsJsonParseSource(parse)) throw new RealtimeDataFormatError();
  const fields = decimalFields(path);
  return parse(text, (key, value, context) => {
    if (typeof value !== 'number' || !fields.has(key)) return value;
    if (typeof context?.source !== 'string') throw new RealtimeDataFormatError();
    return context.source;
  });
}
