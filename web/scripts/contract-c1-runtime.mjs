import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  assertContractCitation, assertContractPublications, fixtureChildEnvironment,
  fixtureEvidenceSecrets, protectPrivateFile, recordFixtureBrowserIdentity,
  recordFixtureCommand, sanitizeFixtureEvidence, writePrivateFile,
} from './contract-synthetic-fixture.mjs';
import { contractBrowserSetup, mergeContractFailures, publishContractCheckpoint } from './reset-contract-runtime-support.mjs';

const IMAGE = 'mcr.microsoft.com/playwright:v1.63.0-noble';
const IMAGE_ID = 'sha256:eff16c30e6f3f4af0a03fa4b706120d5e9b0891c344a27d64559aff5900a4a27';
const pause = ms => new Promise(done => setTimeout(done, ms));
const requireProof = (value, message) => { if (!value) throw new Error(message); };

export async function runC1Contract({ identity, root, compose, run, ownerCa }) {
  const channel = resolve(identity.state, 'browser-channel');
  mkdirSync(channel);
  const pairFile = resolve(channel, 'pair.txt');
  const copiedPair = resolve(identity.state, 'pair-copy.txt');
  const save = (name, value) => writeFileSync(resolve(channel, name), sanitizeFixtureEvidence(value, fixtureEvidenceSecrets(identity)));
  const retained = (name, value) => writeFileSync(resolve(identity.state, name), sanitizeFixtureEvidence(value, fixtureEvidenceSecrets(identity)));
  const manifest = resolve(identity.state, 'manifest.json');
  function updateManifest(value) {
    writeFileSync(manifest, sanitizeFixtureEvidence({
      ...JSON.parse(readFileSync(manifest)), ...value,
    }, fixtureEvidenceSecrets(identity)));
  }
  const cli = (...args) => JSON.parse(compose('exec', '-T', 'public-api', 'dragon-radar', ...args));
  const helper = (...args) => JSON.parse(compose('exec', '-T', '-e', 'PYTHONPATH=/fixture:/app/src',
    'public-api', 'python', '/fixture/contract-c1-fixture.py', ...args));
  const ownerContainer = compose('ps', '-q', 'owner-caddy').trim();
  requireProof(/^[a-f0-9]{64}$/.test(ownerContainer), 'Actual owner-caddy container identity missing');
  const serviceIds = compose('ps', '-q').trim().split(/\s+/);
  const resources = JSON.parse(run('docker', ['inspect', ...serviceIds])).map(item => ({
    container: item.Id, image: item.Image, service: item.Config.Labels['com.docker.compose.service'],
    labels: item.Config.Labels, networkMode: item.HostConfig.NetworkMode,
    networks: Object.keys(item.NetworkSettings.Networks), ports: item.NetworkSettings.Ports,
    mounts: item.Mounts.map(({ Type, Source, Destination, RW }) => ({ Type, Source, Destination, RW })),
  }));
  const ownerCaddy = compose('exec', '-T', 'owner-caddy', 'cat', '/etc/caddy/Caddyfile');
  requireProof(ownerCaddy === readFileSync(resolve(root, 'containers/owner.Caddyfile'), 'utf8'),
    'Deployed owner Caddy configuration differs from frozen fixture source');
  requireProof(!ownerCaddy.includes('lhb') && ownerCaddy.includes('@api path /api/* /health/*')
    && ownerCaddy.includes('respond @api 404'), 'Owner supplementary LHB default denial is not verified');
  const ownerCaddyProof = { sha256: createHash('sha256').update(ownerCaddy).digest('hex'), denyLhb: true };
  retained('c1-owner-caddy.json', ownerCaddyProof);
  const serviceImages = JSON.parse(run('docker', ['image', 'inspect', ...new Set(resources.map(item => item.image))]))
    .map(({ Id, RepoDigests, Os, Architecture }) => ({ Id, RepoDigests, Os, Architecture }));
  retained('c1-resource-identities.json', { resources, images: serviceImages });
  const images = JSON.parse(run('docker', ['image', 'inspect', IMAGE]));
  requireProof(images.length === 1 && images[0].Id === IMAGE_ID
    && images[0].Os === 'linux' && images[0].Architecture === 'amd64'
    && images[0].RepoDigests?.length, 'Locked Linux browser image identity mismatch');
  retained('c1-browser-image.json', images.map(({ Id, RepoDigests, Os, Architecture }) => ({ Id, RepoDigests, Os, Architecture })));
  writeFileSync(resolve(channel, 'root.crt'), ownerCa);
  retained('c1-ca.json', { authority: 'https://localhost:8443',
    sha256: createHash('sha256').update(ownerCa).digest('hex') });
  compose('exec', '-T', '--user', 'root', 'public-api', 'mkdir', '/fixture');
  for (const [path, destination] of [
    ['web/scripts/contract-c1-fixture.py', 'contract-c1-fixture.py'],
    ['tests/factories.py', 'factories.py'], ['tests/topic_factories.py', 'topic_factories.py'],
    ['tests/news_factories.py', 'news_factories.py'],
  ]) compose('cp', resolve(root, path), `public-api:/fixture/${destination}`);
  compose('exec', '-T', '--user', 'root', 'public-api', 'chown', '-R', '10001:10001', '/fixture');
  const databaseProof = helper('database-proof');
  requireProof(databaseProof.database === 'dragon_test' && databaseProof.schema, 'Actual C1 database/schema proof missing');
  retained('c1-database.json', databaseProof);
  updateManifest({ scope: 'C0 transport/isolation + C1 real archived browser lifecycle',
    database: { host: 'db', name: databaseProof.database, schema: databaseProof.schema, migration: databaseProof.migration } });
  const inputs = helper('generate', '--output', '/fixture/inputs');
  compose('cp', 'public-api:/fixture/inputs', resolve(identity.state, 'inputs'));
  retained('c1-inputs.json', inputs);
  updateManifest({ inputs });
  cli('source-register', '/fixture/inputs/grant.json');

  function publish(index, cutoff) {
    const marketImport = cli('import', `/fixture/inputs/market-${index}.json`, '--key', `synthetic-c1-market-${index}`);
    const market = cli('publish', marketImport.batch_id);
    const topicImport = cli('topic-import', `/fixture/inputs/topic-${index}.json`, '--key', `synthetic-c1-topic-${index}`);
    const topic = cli('topic-publish', topicImport.batch_id);
    const news = helper('news', '--inputs', '/fixture/inputs', '--index', String(index),
      ...(cutoff ? ['--cutoff', cutoff] : []));
    const payload = { trade_date: '2026-09-08', market_snapshot_id: market.snapshot_id,
      topic_snapshot_id: topic.snapshot_id, news_input_id: news.id, disabled_domains: ['regulation', 'role'] };
    const file = resolve(identity.state, `publication-${index}.json`);
    writeFileSync(file, JSON.stringify(payload));
    compose('cp', file, `public-api:/fixture/publication-${index}.json`);
    const receipt = cli('publication-publish', `/fixture/publication-${index}.json`, '--key', `synthetic-c1-publication-${index}`);
    const archive = helper('archive', '--publication', receipt.snapshot_id, '--index', String(index));
    retained(`c1-publication-${index}.json`, { marketImport, market, topicImport, topic, news, payload, receipt, archive });
    return { receipt, archive };
  }

  const p1 = publish(1);
  const association = inputs.publications[0].association_id;
  const original = p1.archive;
  const reviewBefore = helper('audit', '--association', association);
  requireProof(reviewBefore.reviews.length === 0 && reviewBefore.audit_events.length === 0, 'C1 review fixture is not fresh');
  retained('c1-review-before.json', reviewBefore);
  let p2;
  let citation;
  let browserContainer;
  let active;
  let execution;
  const outcome = { synthetic: true, databaseProof, resources, serviceImages, publications: [p1], browsers: {}, review: {}, caSha256: createHash('sha256').update(ownerCa).digest('hex') };
  save('initial.json', { mode: 'public', origin: 'http://public-caddy:8080', publication: p1, association, ownerCaddyProof });

  function startExecution(args) {
    const started = Date.now();
    const processHandle = spawn('docker', args, { cwd: root, env: fixtureChildEnvironment(), windowsHide: true });
    const chunks = { stdout: [], stderr: [] };
    processHandle.stdout.on('data', part => chunks.stdout.push(part));
    processHandle.stderr.on('data', part => chunks.stderr.push(part));
    const state = { done: false };
    state.promise = new Promise((resolveExit, rejectExit) => {
      processHandle.on('error', error => { state.done = true; rejectExit(error); });
      processHandle.on('exit', code => {
        state.done = true;
        state.code = code;
        recordFixtureCommand(identity, { command: 'docker', args, status: code, durationMs: Date.now() - started,
          stdout: Buffer.concat(chunks.stdout).toString(), stderr: Buffer.concat(chunks.stderr).toString() });
        resolveExit(code);
      });
    });
    return state;
  }

  async function checkpoint(name, action) {
    const file = resolve(channel, `${name}.request.json`);
    const deadline = Date.now() + 180_000;
    while (!existsSync(file)) {
      if (execution.done) throw new Error(`C1 browser exited ${execution.code} before ${name}; see retained commands`);
      if (Date.now() > deadline) throw new Error(`C1 browser checkpoint timed out: ${name}`);
      await pause(100);
    }
    const request = JSON.parse(readFileSync(file));
    const answer = await action(request);
    publishContractCheckpoint(resolve(channel, `${name}.response.json`),
      sanitizeFixtureEvidence(answer ?? { complete: true }, fixtureEvidenceSecrets(identity)));
    retained(`c1-checkpoint-${name}.json`, { request, answer });
  }

  async function browser(mode, transitions) {
    browserContainer = `${identity.project}-browser-${mode}`;
    const network = mode === 'owner' ? `container:${ownerContainer}` : `${identity.project}_public-edge`;
    const id = run('docker', ['run', '-d', '--name', browserContainer,
      '--label', `com.docker.compose.project=${identity.project}`, '--label', `dragon.fixture.run=${identity.runId}`,
      '--network', network, '--mount', `type=bind,src=${channel},dst=/fixture`,
      IMAGE, 'sleep', 'infinity']).trim();
    active = id;
    requireProof(/^[a-f0-9]{64}$/.test(id), 'Standalone browser container identity missing');
    run('docker', ['exec', id, 'mkdir', '-p', '/work/web/tests/e2e', '/work/web/scripts']);
    for (const path of ['package.json', 'package-lock.json', 'playwright.contract.config.ts',
      'tests/e2e/reset-contract-runtime.spec.ts', 'scripts/contract-c1-pretrust.mjs',
      'scripts/contract-c1-reporter.mjs', 'scripts/reset-contract-runtime-support.mjs',
      'scripts/reset-contract-runtime-support.d.mts',
      'scripts/contract-synthetic-fixture.mjs']) {
      run('docker', ['cp', resolve(root, 'web', path), `${id}:/work/web/${path}`]);
    }
    run('docker', ['exec', '-w', '/work/web', id, 'npm', 'ci', '--ignore-scripts', '--cache', '/tmp/npm-cache']);
    const hostUid = process.platform === 'linux' ? process.getuid?.() : undefined;
    const hostGid = process.platform === 'linux' ? process.getgid?.() : undefined;
    if (Number.isSafeInteger(hostUid) && hostUid > 0 && Number.isSafeInteger(hostGid)) {
      run('docker', ['exec', id, 'groupmod', '-o', '-g', String(hostGid), 'pwuser']);
      run('docker', ['exec', id, 'usermod', '-o', '-u', String(hostUid), 'pwuser']);
    }
    run('docker', ['exec', id, 'chown', '-R', 'pwuser:pwuser', '/home/pwuser', '/work', '/fixture']);
    if (mode === 'owner') {
      const pretrust = JSON.parse(run('docker', ['exec', '--user', 'pwuser', '-e', 'HOME=/home/pwuser',
        '-w', '/work/web', id, 'node', 'scripts/contract-c1-pretrust.mjs']));
      requireProof(pretrust.certificateFailure === 'net::ERR_CERT_AUTHORITY_INVALID', 'Pre-trust TLS failure not proved');
      outcome.pretrust = pretrust;
      retained('c1-pretrust.json', pretrust);
    }
    run('docker', ['exec', id, 'bash', '-ec', contractBrowserSetup(mode)], { timeout: 240_000 });
    execution = startExecution(['exec', '--user', 'pwuser', '-e', 'HOME=/home/pwuser',
      ...(mode === 'owner' ? ['-e', 'NODE_EXTRA_CA_CERTS=/fixture/root.crt'] : []),
      '-w', '/work/web', id, 'xvfb-run', '-a', 'npx', '--no-install', 'playwright',
      'test', '--config', 'playwright.contract.config.ts']);
    let failure;
    try {
      await transitions();
      const deadline = Date.now() + 180_000;
      while (!execution.done && Date.now() < deadline) await pause(100);
      requireProof(execution.done, 'C1 browser did not terminate');
      requireProof(await execution.promise === 0, `C1 ${mode} browser failed; see commands.jsonl`);
      const proof = JSON.parse(readFileSync(resolve(channel, `${mode}-evidence.json`)));
      outcome.browsers[mode] = proof;
      recordFixtureBrowserIdentity(identity, { channel: 'chromium', version: proof.chromium });
      retained(`c1-browser-${mode}.json`, proof);
    } catch (error) {
      failure = error;
    }
    try { await removeBrowser(); } catch (error) { failure = mergeContractFailures(failure, error); }
    if (failure) throw failure;
  }

  async function removeBrowser() {
    if (!active) return;
    const inspect = JSON.parse(run('docker', ['inspect', active]))[0];
    requireProof(inspect.Config.Labels['com.docker.compose.project'] === identity.project
      && inspect.Config.Labels['dragon.fixture.run'] === identity.runId, 'Refusing mismatched browser cleanup');
    run('docker', ['rm', '-f', active]);
    retained(`c1-browser-cleanup-${browserContainer.endsWith('owner') ? 'owner' : 'public'}.json`,
      { id: active, project: identity.project, runId: identity.runId, status: 'removed' });
    active = undefined;
    if (execution) await execution.promise;
  }

  let primary;
  try {
    // Public positive reads precede owner revocation; browser namespaces never overlap.
    await browser('public', async () => {});
    save('initial.json', { mode: 'owner', origin: 'https://localhost:8443', publication: p1, association, ownerCaddyProof });
    compose('exec', '-T', 'owner-api', 'dragon-radar', 'owner-pair', '--origin', 'https://localhost:8443', '--output', '/tmp/browser-pair.txt');
    compose('cp', 'owner-api:/tmp/browser-pair.txt', copiedPair);
    const copyPermissions = protectPrivateFile(copiedPair);
    const code = readFileSync(copiedPair, 'utf8').trim();
    fixtureEvidenceSecrets(identity).push(code);
    const browserPermissions = writePrivateFile(pairFile, code);
    retained('c1-pair-permissions.json', { copyPermissions, browserPermissions });
    rmSync(copiedPair);
    compose('exec', '-T', 'owner-api', 'rm', '/tmp/browser-pair.txt');
    await browser('owner', async () => {
      await checkpoint('paired', () => { rmSync(pairFile); });
      await checkpoint('reviewed', request => {
        const after = helper('archive', '--publication', p1.receipt.snapshot_id);
        requireProof(after.raw_sha256 === original.raw_sha256
          && JSON.stringify(after.materialization_hashes) === JSON.stringify(original.materialization_hashes),
        'Owner review mutated original publication/raw rules');
        const auditAfter = helper('audit', '--association', association);
        requireProof(auditAfter.reviews.length === 1 && auditAfter.audit_events.length === 1
          && auditAfter.reviews[0].decision === 'accept'
          && auditAfter.audit_events[0].id === request.audit_id, 'Independent owner review audit receipt mismatch');
        outcome.review = { before: reviewBefore, after: auditAfter, p1Before: original.raw_sha256, p1AfterReview: after.raw_sha256 };
        p2 = publish(2, auditAfter.captured_at);
        const old = helper('archive', '--publication', p1.receipt.snapshot_id);
        requireProof(old.raw_sha256 === original.raw_sha256, 'Revision publication mutated P1 archive');
        const news1 = old.results.find(row => row.domain === 'news').payload.articles;
        const news2 = p2.archive.results.find(row => row.domain === 'news').payload.articles;
        const link1 = news1.find(item => item.article_id === p1.archive.article_id).topics.find(item => item.association_id === association);
        const link2 = news2.find(item => item.article_id === p1.archive.article_id).topics.find(item => item.association_id === association);
        requireProof(link1.status === 'unreviewed' && link1.effective === false
          && link2.status === 'accepted' && link2.effective === true, 'Only P2 must materialize accepted/effective review');
        outcome.publications.push(p2);
        assertContractPublications(outcome);
        updateManifest({ inputs: { ...inputs, publications: outcome.publications } });
        retained('c1-bound-publications.json', outcome.publications);
        return { publication: p2, review: outcome.review };
      });
      await checkpoint('task-submitted', request => {
        citation = helper('complete-task', '--task', request.task_id);
        return citation;
      });
      await checkpoint('citation-opened', request => {
        citation = { ...citation, status: 'ready', resolved: request.resolved };
        assertContractCitation(p1, citation);
        outcome.citation = citation;
        return { complete: true };
      });
      await checkpoint('source-revoke', () => {
        cli('source-revoke', 'synthetic-c1');
        return { complete: true };
      });
      await checkpoint('owner-revoke', () => {
        compose('exec', '-T', 'owner-api', 'dragon-radar', 'owner-revoke');
        return { complete: true };
      });
    });
    const finalArchive = helper('archive', '--publication', p1.receipt.snapshot_id);
    requireProof(finalArchive.raw_sha256 === original.raw_sha256, 'Revocation mutated archived P1 bytes');
    outcome.review.p1AfterRevocation = finalArchive.raw_sha256;
    retained('c1-outcome.json', outcome);
    return outcome;
  } catch (error) {
    primary = error;
    throw error;
  } finally {
    let failure;
    for (const cleanup of [
      () => removeBrowser(),
      () => rmSync(pairFile, { force: true }),
      () => rmSync(copiedPair, { force: true }),
    ]) {
      try { await cleanup(); } catch (error) { failure = mergeContractFailures(failure, error); }
    }
    if (failure) throw mergeContractFailures(primary, failure);
  }
}
