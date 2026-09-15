let guard: (() => boolean) | undefined;
let generation = 0;
export function registerLeaveGuard(next: () => boolean) {
  guard = next;
  return () => { if (guard === next) guard = undefined; };
}
export function captureNavigationContext() { return generation; }
export function isNavigationContextCurrent(context: number) { return context === generation; }
export function allowNavigation() {
  const allowed = guard ? guard() : true;
  if (allowed) generation++;
  return allowed;
}
