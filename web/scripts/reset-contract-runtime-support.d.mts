export function publishContractCheckpoint(path: string, serialized: string): void;
export function isContractLhbDenial(
  error: { url: string; method: string; status: number; body?: string; transition: string },
  publications: readonly {
    archive: { security_id: string };
    receipt: { published_at: string };
  }[],
): boolean;
