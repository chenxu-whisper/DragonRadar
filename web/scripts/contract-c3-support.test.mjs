import { test } from 'node:test';
import assert from 'node:assert/strict';
import { approvedSyntheticTarget } from './contract-c3-support.mjs';

test('C3 synthetic transport accepts only its exact run-bound target', () => {
  const approved = 'http://127.0.0.1:12345/synthetic/c3-run/exact';
  assert.equal(approvedSyntheticTarget(approved, approved), approved);
  for (const target of [
    'http://user:password@127.0.0.1:12345/synthetic/c3-run/exact',
    `${approved}?redirect=1`, `${approved}/extra`,
    approved.replace('12345', '12346'), approved.replace('c3-run', 'other'),
    'https://example.com/synthetic/c3-run/exact',
  ]) assert.throws(() => approvedSyntheticTarget(target, approved), /target/i);
});
