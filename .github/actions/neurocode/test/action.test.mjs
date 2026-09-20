#!/usr/bin/env node
// The NeuroCode action, run here, with a stub `nc`.
//
// A workflow step is the worst place to find a mistake: the run that proves it wrong is the release
// you needed. So the body of this action is a script, and it is run below against a `nc` that prints
// what the test tells it to and exits with the code the test tells it to — which is the whole of
// what this action has to understand about NeuroCode, and the only part of it that can be wrong
// without anybody seeing.
//
// The case that matters most: `nc` exits 3 when a gate, a permission card or a signature is waiting.
// A workflow that read that as success would be a green tick over work nobody has looked at.
//
//   node --test .github/actions/neurocode/test/
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const ACTION = path.join(here, '..');
const RUN = path.join(ACTION, 'run.sh');

/** A `nc` that records how it was called, prints `says`, and exits with `code`. */
function stub({ says = '{}', code = 0, version = '{"nc": "0.1.0"}' } = {}) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'nc-action-'));
  const bin = path.join(home, 'bin');
  fs.mkdirSync(bin);
  // What it prints goes in a file rather than in the script, so a multi-line answer — which is what
  // `--watch --json` really produces — stays multi-line instead of becoming a backslash and an n.
  fs.writeFileSync(path.join(home, 'says'), `${says}\n`);
  fs.writeFileSync(path.join(home, 'version'), `${version}\n`);
  fs.writeFileSync(
    path.join(bin, 'nc'),
    ['#!/usr/bin/env bash',
     `if [ "$1" = version ]; then cat ${JSON.stringify(path.join(home, 'version'))}; exit 0; fi`,
     `printf '%s\\n' "$*" >> ${JSON.stringify(path.join(home, 'called'))}`,
     `cat ${JSON.stringify(path.join(home, 'says'))}`,
     `exit ${code}`].join('\n'),
    { mode: 0o755 },
  );
  return { home, bin, output: path.join(home, 'output') };
}

/** Run the action's body. Answers its exit code, what it printed, and the outputs it set. */
function act(inputs, options = {}) {
  const it = stub(options);
  const env = {
    PATH: `${it.bin}:/usr/bin:/bin:/usr/sbin:/sbin`,
    HOME: it.home,
    GITHUB_OUTPUT: it.output,
    INPUT_SERVER: 'https://nc.example.com',
    INPUT_TOKEN: 'nc_pat_secret',
    ...inputs,
  };
  for (const [key, value] of Object.entries(env)) if (value === undefined) delete env[key];
  let status = 0;
  let said = '';
  try {
    said = execFileSync('bash', [RUN], { env, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  } catch (e) {
    status = e.status;
    said = `${e.stdout ?? ''}${e.stderr ?? ''}`;
  }
  const raw = fs.existsSync(it.output) ? fs.readFileSync(it.output, 'utf8') : '';
  const outputs = Object.fromEntries(
    [...raw.matchAll(/^([a-z-]+)=(.*)$/gm)].map((m) => [m[1], m[2]]),
  );
  const block = raw.match(/^json<<(\S+)\n([\s\S]*?)\n\1$/m);
  if (block) outputs.json = block[2];
  const called = fs.existsSync(path.join(it.home, 'called'))
    ? fs.readFileSync(path.join(it.home, 'called'), 'utf8').trim()
    : '';
  return { status, said, outputs, called };
}

/* ── the one that matters ──────────────────────────────────────── */

test('a run that stopped at a person fails the job, and says what to do', () => {
  const done = act(
    { INPUT_COMMAND: 'wait', INPUT_RUN: 'RUN-7' },
    { code: 3, says: '{"kind":"run","ref":"RUN-7","status":"waiting"}' },
  );
  assert.equal(done.status, 1);
  assert.match(done.said, /::error::/);
  assert.match(done.said, /waiting on a person/);
  assert.match(done.said, /RUN-7/);
  assert.match(done.said, /Nothing has been merged/);
  assert.equal(done.outputs.waiting, 'true');
  assert.equal(done.outputs.ref, 'RUN-7');
  assert.equal(done.outputs.status, 'waiting');
  assert.equal(done.outputs['exit-code'], '3');
});

test('and carries on when the workflow said it should, still saying so', () => {
  const done = act(
    { INPUT_COMMAND: 'wait', INPUT_RUN: 'RUN-7', INPUT_FAIL_ON_WAITING: 'false' },
    { code: 3, says: '{"ref":"RUN-7","status":"waiting"}' },
  );
  assert.equal(done.status, 0);
  assert.match(done.said, /::notice::/);
  assert.equal(done.outputs.waiting, 'true');
});

test('gates sitting in the inbox are waiting on a person too, however cheerfully nc exits', () => {
  const done = act({ INPUT_COMMAND: 'approvals' }, { code: 0, says: '[{"ref":"APR-1"},{"ref":"APR-2"}]' });
  assert.equal(done.status, 1);
  assert.match(done.said, /2 gate\(s\) are waiting/);
  assert.equal(done.outputs.waiting, 'true');
});

test('an empty inbox is not a failure', () => {
  const done = act({ INPUT_COMMAND: 'approvals' }, { code: 0, says: '[]' });
  assert.equal(done.status, 0);
  assert.equal(done.outputs.waiting, 'false');
});

/* ── the commands ──────────────────────────────────────────────── */

test('compile asks nc to compile, and hands back the plan', () => {
  const done = act(
    { INPUT_COMMAND: 'compile', INPUT_PROJECT: 'PRJ-1', INPUT_REQUIREMENT: 'credit notes must round once' },
    { says: '{"ref":"PLAN-12","status":"draft"}' },
  );
  assert.equal(done.status, 0);
  assert.equal(done.called, 'plan credit notes must round once --project PRJ-1 --json');
  assert.equal(done.outputs.ref, 'PLAN-12');
  assert.equal(done.outputs.json, '{"ref":"PLAN-12","status":"draft"}');
});

test('dispatch hands back the run, not the plan — that is what the next step waits on', () => {
  const done = act(
    { INPUT_COMMAND: 'dispatch', INPUT_PLAN: 'PLAN-12', INPUT_GOAL_BUDGET: '3' },
    { says: '{"ref":"PLAN-12","runRef":"RUN-7","status":"dispatched"}' },
  );
  assert.equal(done.called, 'dispatch PLAN-12 --goal 3 --json');
  assert.equal(done.outputs.ref, 'RUN-7');
});

test('wait follows one run with a ceiling, and the last line is the answer', () => {
  const done = act(
    { INPUT_COMMAND: 'wait', INPUT_RUN: 'RUN-7', INPUT_TIMEOUT: '600' },
    { says: '{"kind":"log","line":"writing"}\n{"kind":"run","ref":"RUN-7","status":"done"}' },
  );
  assert.equal(done.called, 'runs RUN-7 --watch --timeout 600 --json');
  assert.equal(done.outputs.ref, 'RUN-7');
  assert.equal(done.outputs.status, 'done');
  assert.equal(done.status, 0);
});

test('watching that gave up says the run is still going, and that nothing was stopped', () => {
  const done = act(
    { INPUT_COMMAND: 'wait', INPUT_RUN: 'RUN-7', INPUT_TIMEOUT: '30' },
    { code: 4, says: '{"kind":"run","ref":"RUN-7","status":"running"}' },
  );
  assert.equal(done.status, 1);
  assert.match(done.said, /still going after 30s/);
  assert.match(done.said, /Nothing was stopped/);
  assert.equal(done.outputs.waiting, 'false');
});

test('the escape hatch passes your own arguments through, quotes and all', () => {
  const done = act({ INPUT_COMMAND: 'nc', INPUT_ARGS: 'memory search "credit notes"' }, { says: '[]' });
  assert.equal(done.called, 'memory search credit notes --json');
});

/* ── refusals, in words ────────────────────────────────────────── */

test('every missing input is named, and nc is never called', () => {
  for (const [inputs, words] of [
    [{ INPUT_COMMAND: 'compile', INPUT_PROJECT: 'PRJ-1' }, /requirement/],
    [{ INPUT_COMMAND: 'compile', INPUT_REQUIREMENT: 'x' }, /project/],
    [{ INPUT_COMMAND: 'dispatch' }, /plan/],
    [{ INPUT_COMMAND: 'wait' }, /run/],
    [{ INPUT_COMMAND: 'nc' }, /args/],
    [{}, /command/],
    [{ INPUT_COMMAND: 'compile', INPUT_PROJECT: 'P', INPUT_REQUIREMENT_FILE: '/no/such/file' }, /No such file/],
  ]) {
    const done = act(inputs);
    assert.equal(done.status, 1, JSON.stringify(inputs));
    assert.match(done.said, /::error::/);
    assert.match(done.said, words);
    assert.equal(done.called, '');
  }
});

test('a command it does not do is named, with the ones it does', () => {
  const done = act({ INPUT_COMMAND: 'merge' });
  assert.equal(done.status, 1);
  assert.match(done.said, /'merge' is not something this action does/);
  assert.match(done.said, /status, compile, dispatch, wait, approvals, nc/);
});

test('no server and no token are two different sentences', () => {
  assert.match(act({ INPUT_COMMAND: 'status', INPUT_SERVER: undefined }).said, /needs 'server'/);
  assert.match(act({ INPUT_COMMAND: 'status', INPUT_TOKEN: undefined }).said, /Never a password/);
});

test('the nc on the PATH is checked: netcat is not NeuroCode', () => {
  const done = act({ INPUT_COMMAND: 'status' }, { version: 'usage: nc [-46CDdFhklNnrStUuvZz]' });
  assert.equal(done.status, 1);
  assert.match(done.said, /is not NeuroCode's/);
  assert.match(done.said, /netcat/);
});

test('a refusal from the server fails the job and leaves nc’s own words to speak', () => {
  const done = act({ INPUT_COMMAND: 'status' }, { code: 1, says: '{}' });
  assert.equal(done.status, 1);
  assert.match(done.said, /its own words are above/);
});

/* ── the manifest and the script agree ─────────────────────────── */

test('every input the script reads is an input the action declares', () => {
  const manifest = fs.readFileSync(path.join(ACTION, 'action.yml'), 'utf8');
  const script = fs.readFileSync(RUN, 'utf8');
  for (const [, name] of script.matchAll(/INPUT_([A-Z_]+)/g)) {
    assert.match(manifest, new RegExp(`INPUT_${name}:`), `INPUT_${name} is read but never set`);
  }
  // And nothing declared is left unwired — an input that does nothing is a lie in a table.
  for (const [, name] of manifest.matchAll(/^\s{8}INPUT_([A-Z_]+):/gm)) {
    assert.match(script, new RegExp(`INPUT_${name}`), `INPUT_${name} is passed in and never read`);
  }
});

test('nothing here can sign for a person, including the escape hatch', () => {
  // nc has no --yes and never will: it would mean a signature nobody read and a merge nobody
  // watched. A workflow that could type `nc accept` would be that --yes by another route.
  const script = fs.readFileSync(RUN, 'utf8');
  // Comments dropped: the script says in prose why there is no --yes, which is not a --yes.
  const code = script.split('\n').filter((line) => !line.trim().startsWith('#')).join('\n');
  assert.doesNotMatch(code, /--yes/);
  for (const [, built] of script.matchAll(/argv=\(([^)]*)\)/g)) {
    assert.doesNotMatch(built, /\b(accept|approve|deny|merge|push|send-back)\b/, built);
  }
  for (const word of ['accept RUN-7', 'merge RUN-7', 'push RUN-7', 'approve APR-1', 'deny APR-1',
                      'send-back RUN-7 --notes x']) {
    const done = act({ INPUT_COMMAND: 'nc', INPUT_ARGS: word });
    assert.equal(done.status, 1, word);
    assert.match(done.said, /a person's to type/);
    assert.equal(done.called, '', word);
  }
});
