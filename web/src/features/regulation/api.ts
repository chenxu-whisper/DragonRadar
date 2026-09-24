export function regulationStatusPath(date: string, snapshot: string): string {
  return `/api/regulation/status?${new URLSearchParams({
    date,
    snapshot_id: snapshot,
  })}`;
}

export function regulationEventsPath(date: string, snapshot: string): string {
  return `/api/regulation/events?${new URLSearchParams({
    date,
    snapshot_id: snapshot,
  })}`;
}
