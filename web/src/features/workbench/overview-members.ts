export type MemberSort = 'security_id' | 'code' | 'name';
interface Member { security_id: string; code: string | null; name: string | null }
const compare = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0;
export function sortOverviewMembers<T extends Member>(rows: readonly T[], field: MemberSort, order: 'asc' | 'desc'): T[] {
  return [...rows].sort((left, right) => {
    const a = left[field], b = right[field];
    const missingA = a === null || !a.trim(), missingB = b === null || !b.trim();
    if (missingA !== missingB) return missingA ? 1 : -1;
    const result = missingA ? 0 : compare(a!, b!);
    return (order === 'asc' ? result : -result) || compare(left.security_id, right.security_id);
  });
}
