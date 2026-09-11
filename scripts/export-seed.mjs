#!/usr/bin/env node
// Writes server/seed/seed.json from the TypeScript mocks, so the API and the static demo share one
// source of truth for the domain — they cannot drift apart.   npm run seed
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const MOCKS = path.join(ROOT, 'src/mock');
const OUT = path.join(ROOT, 'node_modules/.cache/nc-seed');
const require = createRequire(import.meta.url);
const ts = require(path.join(ROOT, 'node_modules/typescript'));

// Every mock is transpiled, so a mock that imports another one always resolves.
fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT, { recursive: true });
fs.writeFileSync(path.join(OUT, 'package.json'), '{ "type": "commonjs" }\n');
for (const file of fs.readdirSync(MOCKS).filter((f) => f.endsWith('.ts'))) {
  let js = ts.transpileModule(fs.readFileSync(path.join(MOCKS, file), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  js = js.replace(/require\("@\/mock\/([\w-]+)"\)/g, 'require("./$1")');
  fs.writeFileSync(path.join(OUT, file.replace(/\.ts$/, '.js')), js);
}
const m = (f) => require(path.join(OUT, `${f}.js`));

const seed = {
  projects: m('projects').projects,
  agents: m('agents').agents,
  tasks: m('tasks').tasks,
  approvals: m('permissions').approvals,
  permissionRules: m('permissions').permissionRules,
  memory: m('memory').memoryFacts,
  plans: m('plans').plans,
  conflicts: m('memory').memoryConflicts,
  mcp: m('mcp').mcpServers,
  // newest first — the order the Activity screen renders
  activity: [...m('activity').activity].reverse().concat(m('activity-extra').activityExtra),
};
const dest = path.join(ROOT, 'server/seed/seed.json');
fs.mkdirSync(path.dirname(dest), { recursive: true });
fs.writeFileSync(dest, JSON.stringify(seed, null, 1) + '\n');
console.log('seed:', Object.entries(seed).map(([k, v]) => `${k} ${v.length}`).join(' · '), '→', path.relative(ROOT, dest));
