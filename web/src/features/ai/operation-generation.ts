export interface TaskOperation {
  generation: number;
  signal: AbortSignal;
  controller: AbortController;
}

export class TaskOperationGeneration {
  private generation = 0;
  private active = new Set<AbortController>();

  get current(): number {
    return this.generation;
  }

  begin(): TaskOperation {
    const controller = new AbortController();
    this.active.add(controller);
    return {
      generation: this.generation,
      signal: controller.signal,
      controller,
    };
  }

  isCurrent(operation: TaskOperation): boolean {
    return operation.generation === this.generation
      && !operation.signal.aborted;
  }

  finish(operation: TaskOperation): boolean {
    this.active.delete(operation.controller);
    return this.isCurrent(operation);
  }

  invalidate(): void {
    this.generation++;
    for (const controller of this.active) controller.abort();
    this.active.clear();
  }
}
