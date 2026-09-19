// Makes desktop/app runnable: compiles src/ into app/out with the desktop's own tsconfig, and writes the two
// files Electron and electron-builder read beside it — app/package.json (name, version, entry point) and
// app/build.json (where this checkout is, so a packed app can find the server it starts).
//   node desktop/scripts/prepare.mjs
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const DESKTOP = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const ROOT = path.resolve(DESKTOP, '..');
const APP = path.join(DESKTOP, 'app');

export function prepare() {
  const root = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));
  fs.rmSync(path.join(APP, 'out'), { recursive: true, force: true });
  const tsc = spawnSync(path.join(ROOT, 'node_modules/.bin/tsc'), ['-p', path.join(DESKTOP, 'tsconfig.json')], { stdio: 'inherit' });
  if (tsc.status !== 0) throw new Error('The desktop app did not compile (see above).');
  // The app's own package.json names no dependencies: everything it runs is Electron's or Node's own, so
  // electron-builder packs nothing from node_modules — the web app it shows is already bundled in dist/.
  const pkg = {
    name: 'neurocode-desktop',
    productName: 'NeuroCode',
    version: root.version,
    description: 'NeuroCode, the AI engineering OS, on the desktop.',
    author: 'NeuroSynth Labs',
    private: true,
    type: 'module',
    main: 'out/main.js',
  };
  fs.writeFileSync(path.join(APP, 'package.json'), `${JSON.stringify(pkg, null, 2)}\n`);
  fs.writeFileSync(path.join(APP, 'build.json'), `${JSON.stringify({ home: ROOT, version: root.version }, null, 2)}\n`);
  return pkg;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const pkg = prepare();
  console.log(`✓ desktop/app ready · NeuroCode ${pkg.version}`);
}
