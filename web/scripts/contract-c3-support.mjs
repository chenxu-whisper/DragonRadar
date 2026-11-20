export function approvedSyntheticTarget(target, approved) {
  const url = new URL(target);
  if (target !== approved || url.username || url.password || url.search || url.hash
    || url.protocol !== 'http:' || url.hostname !== '127.0.0.1'
    || !/^\/synthetic\/[a-z0-9-]+\/exact$/.test(url.pathname)) {
    throw new Error('Unapproved synthetic target');
  }
  return target;
}
