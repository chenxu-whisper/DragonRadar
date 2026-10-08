import type {
  LocalNote,
  SaveNote,
} from '../../repositories/local/repository';

export type AdoptionStrategy = 'replace' | 'merge' | 'save_as_new';

type AdoptionPayload = Omit<SaveNote, 'note_id' | 'key'> & {
  note_id?: string;
};

interface AdoptionRequest {
  taskRevision: number;
  strategy: AdoptionStrategy;
  payload: AdoptionPayload;
}

interface NoteWriter {
  putNote(command: SaveNote): Promise<LocalNote>;
}

function canonical(value: unknown): string {
  if (value === undefined) return '"$undefined"';
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.entries(value)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`)
      .join(',')}}`;
  }
  return JSON.stringify(value);
}

export class AdoptionCommandCache {
  private pending = new Map<string, SaveNote>();

  async execute(repository: NoteWriter, request: AdoptionRequest): Promise<LocalNote> {
    const signature = canonical([
      request.taskRevision,
      request.strategy,
      request.payload,
    ]);
    let command = this.pending.get(signature);
    if (!command) {
      command = {
        ...request.payload,
        note_id: request.payload.note_id ?? crypto.randomUUID(),
        key: crypto.randomUUID(),
      };
      this.pending.set(signature, command);
    }
    const result = await repository.putNote(command);
    if (this.pending.get(signature) === command) this.pending.delete(signature);
    return result;
  }
}
