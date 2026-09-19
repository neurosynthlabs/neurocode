// The whole stack on a throwaway database, for the checks that need a real app to look at:
// the end-to-end run, the render smoke and the layout lint.
//
// There is no sample workspace to fall back on any more, so every check starts the real thing — Postgres
// emptied and migrated, the API, the web app proxying to it, and a stub model on a lane so the model paths
// are walked without a key or the network. What a check needs to exist, it creates: through the API, or
// with `fixture: true`, the tests' own fixture workspace.
//
//   const stack = await startStack({ name: 'smoke', apiPort: 8796, webPort: 5196, web: 'preview', fixture: true });
//   ... stack.api, stack.web, stack.token, stack.model.seen ...
//   await stack.stop();
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { startStubModel } from './stub-model.mjs';

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PYTHON = path.join(ROOT, 'server/.venv/bin/python');

/** Every lane's key variable, so a key exported in this shell can never reach a real provider from a check. */
const LANE_KEYS = ['GROQ_API_KEY', 'CEREBRAS_API_KEY', 'GEMINI_API_KEY', 'MISTRAL_API_KEY', 'OPENROUTER_API_KEY',
  'GITHUB_TOKEN', 'GITHUB_MODELS_TOKEN', 'DEEPSEEK_API_KEY'];

export const OWNER = { workspace: 'Check Works', name: 'Asha Rao', email: 'asha@check.test', password: 'check-owner-password' };

async function waitFor(url, what, alive, ms = 90000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const dead = alive();
    if (dead) throw new Error(`${what} exited before it came up:\n${dead}`);
    try { if ((await fetch(url)).status < 500) return; } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error(`${what} did not come up at ${url}`);
}

function python(args, env, cwd = path.join(ROOT, 'server')) {
  const r = spawnSync(PYTHON, args, { cwd, encoding: 'utf8', env: { ...process.env, ...env } });
  if (r.error || r.status !== 0) throw new Error(`${args.join(' ')} failed:\n${(r.stderr || r.stdout || '').slice(-1500)}`);
}

/** A Claude-style home with one skill, so the extension screens read a known folder rather than this machine's. */
function claudeHome(dir) {
  const skill = path.join(dir, 'skills', 'tidy-imports');
  fs.mkdirSync(skill, { recursive: true });
  fs.writeFileSync(path.join(skill, 'SKILL.md'),
    '---\nname: tidy-imports\ndescription: Sort and group imports the way the file already does.\n---\n\nKeep the groups the file uses.\n');
  return dir;
}

export async function startStack({ name, apiPort, webPort, web = 'dev', fixture = false, owner = false }) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), `nc-${name}-`));
  const db = `neurocode_${name}`;
  const url = `postgresql+asyncpg://neurocode:neurocode@127.0.0.1:5432/${db}`;
  const procs = [];

  python([path.join(ROOT, 'scripts/bootstrap-db.py'), '--reset', db], {}, ROOT);
  python(['-m', 'alembic', 'upgrade', 'head'], { NEUROCODE_DATABASE_URL: url });
  if (fixture) python([path.join(ROOT, 'scripts/load-fixture.py'), '--database', url], {}, ROOT);

  const model = await startStubModel();
  const spawnLogged = (label, cmd, args, env) => {
    const p = spawn(cmd, args, { cwd: ROOT, env: { ...process.env, ...env }, stdio: ['ignore', 'pipe', 'pipe'] });
    p.log = '';
    p.stdout.on('data', (d) => { p.log = (p.log + d).slice(-4000); });
    p.stderr.on('data', (d) => { p.log = (p.log + d).slice(-4000); });
    p.label = label;
    procs.push(p);
    return p;
  };
  const dead = (p) => () => (p.exitCode !== null ? `${p.label} (exit ${p.exitCode}):\n${p.log}` : null);

  const api = spawnLogged('api', PYTHON,
    ['-m', 'uvicorn', 'app.api.app:create_api', '--factory', '--app-dir', 'server', '--host', '127.0.0.1',
      '--port', String(apiPort), '--timeout-graceful-shutdown', '5'],
    {
      NEUROCODE_DATABASE_URL: url,
      // Nothing from this machine: no .env, no saved keys, no Claude folder, no pinned compiler.
      NEUROCODE_SECRETS_PATH: path.join(tmp, 'secrets.json'),
      NEUROCODE_BACKUPS_DIR: path.join(tmp, 'backups'),
      // Runs and clones go here too, so a test stack never leaves worktrees in server/.worktrees.
      NEUROCODE_WORKTREES_DIR: path.join(tmp, 'worktrees'),
      NEUROCODE_REPOS_DIR: path.join(tmp, 'repos'),
      NEUROCODE_CLAUDE_HOME: claudeHome(path.join(tmp, 'claude')),
      NEUROCODE_COMPILER: '',
      // The stack's own temporary folder is where the checks put the repositories they onboard, so it is a root
      // the folder browser and local onboarding may reach, beside the home folder.
      NEUROCODE_MACHINE_ROOTS: `${os.homedir()}:${tmp}`,
      ...Object.fromEntries(LANE_KEYS.map((k) => [k, ''])),
      // The one lane that answers is the stub.
      GROQ_API_KEY: 'stub', NEUROCODE_GROQ_URL: model.url,
    });

  const vite = path.join(ROOT, 'node_modules/vite/bin/vite.js');
  const webArgs = web === 'preview'
    ? [vite, 'preview', '--port', String(webPort), '--strictPort', '--host', '127.0.0.1']
    : [vite, '--port', String(webPort), '--strictPort', '--host', '127.0.0.1'];
  const webProc = spawnLogged('web', process.execPath, webArgs, { NC_API_PORT: String(apiPort) });

  const stack = {
    api: `http://127.0.0.1:${apiPort}`, web: `http://127.0.0.1:${webPort}`, model, tmp, token: '',
    /** The last few thousand characters each process wrote, for a check that needs to say why something failed. */
    logs: () => procs.map((p) => `── ${p.label} ──\n${p.log}`).join('\n'),
    async stop() {
      for (const p of procs) p.kill();
      await model.close();
      fs.rmSync(tmp, { recursive: true, force: true });
    },
  };
  try {
    await waitFor(`${stack.api}/health`, 'the API', dead(api));
    await waitFor(stack.web, 'the web app', dead(webProc));
    if (owner) {
      const r = await fetch(`${stack.api}/auth/setup`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(OWNER),
      });
      if (r.status !== 201) throw new Error(`setting up the owner → ${r.status} ${await r.text()}`);
      stack.token = /nc_session=([^;]+)/.exec(r.headers.get('set-cookie') ?? '')?.[1] ?? '';
    }
  } catch (e) {
    await stack.stop();
    throw e;
  }
  return stack;
}
