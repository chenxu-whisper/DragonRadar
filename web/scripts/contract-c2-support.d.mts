export const variants: Record<string, number[]>;
export const surfaces: string[];
export const targetSets: Record<string, string[]>;
export const metadataColumns: string[];
export const dataColumns: Record<string, string[]>;
export const viewports: { width: number; height: number }[];
export interface MatrixCell {
  variant: string; surface: string; viewport: { width: number; height: number };
  theme: 'light' | 'dark'; zoom: number;
}
export function expectedCells(): MatrixCell[];
export function cellKey(cell: MatrixCell): string;
export function assertMatrix(cells: unknown[]): { cells: number; pairs: number };
export const publication: { snapshotId: string; date: string };
export function digest(value: unknown): string;
export function parseCsv(text: string): Record<string, string>[];
export function assertRunEvidence(evidence: unknown): { cells: number; pairs: number };
export function assertBuildEvidence(evidence: unknown): void;
export function protectSetup<T>(setup: () => Promise<T>, close: () => Promise<unknown>, remove: () => unknown): Promise<T>;
export function finalResult(result: unknown, cleanup: unknown, failure?: unknown): unknown;
export interface TrafficReceipt {
  url: string; method: string; kind: 'cancel' | 'denial'; reason: string; status?: number;
  allowAbortAfterDenial?: boolean;
}
export function createTrafficAudit(): {
  errors: string[]; receipts: (TrafficReceipt & { observed: boolean; assertion: string | null })[];
  expectRequest(request: object, detail: TrafficReceipt): { asserted(assertion: string): void };
  response(request: object, status: number): void;
  failed(request: object, reason: string): void;
  assertClean(): void;
};
