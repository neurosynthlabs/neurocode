// `npm run desktop:build`: the web app is built first (by the npm script), then this packs the desktop app
// around it — compile, the icon from public/neurocode.svg, and electron-builder → an arm64 .dmg in desktop/dist.
//
// Signing: with a "Developer ID Application" identity in the keychain the app is signed with it (and notarised
// when APPLE_ID / APPLE_APP_SPECIFIC_PASSWORD / APPLE_TEAM_ID are set). Without one it is signed ad hoc only —
// enough for Apple silicon to run it on this Mac, but it is NOT a signed app: another Mac's Gatekeeper refuses it
// until it is opened with right-click → Open (or the quarantine flag is removed). The build says which it made.
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { DESKTOP, ROOT, prepare } from './prepare.mjs';

const BUILD = path.join(DESKTOP, 'build');

function step(cmd, args, what) {
  const r = spawnSync(cmd, args, { stdio: 'inherit', cwd: ROOT });
  if (r.status !== 0) throw new Error(`${what} failed (${cmd} ${args.join(' ')})`);
}

/** public/neurocode.svg → a 1024 px PNG (drawn by Electron) → every size macOS wants → icon.icns (sips, iconutil). */
function icon() {
  const svg = path.join(ROOT, 'public/neurocode.svg');
  const icns = path.join(BUILD, 'icon.icns');
  if (fs.existsSync(icns) && fs.statSync(icns).mtimeMs > fs.statSync(svg).mtimeMs) return icns;
  const png = path.join(BUILD, 'icon.png');
  step(path.join(ROOT, 'node_modules/.bin/electron'), [path.join(DESKTOP, 'scripts/icon.mjs'), png], 'Drawing the icon');
  const set = path.join(BUILD, 'icon.iconset');
  fs.rmSync(set, { recursive: true, force: true });
  fs.mkdirSync(set, { recursive: true });
  for (const size of [16, 32, 128, 256, 512]) {
    for (const scale of [1, 2]) {
      const px = size * scale;
      const name = `icon_${size}x${size}${scale === 2 ? '@2x' : ''}.png`;
      step('sips', ['-z', String(px), String(px), png, '--out', path.join(set, name)], `Scaling the icon to ${px} px`);
    }
  }
  step('iconutil', ['-c', 'icns', set, '-o', icns], 'Making icon.icns');
  fs.rmSync(set, { recursive: true, force: true });
  return icns;
}

function identity() {
  const r = spawnSync('security', ['find-identity', '-v', '-p', 'codesigning'], { encoding: 'utf8' });
  const found = /"(Developer ID Application: [^"]+)"/.exec(r.stdout ?? '');
  return found ? found[1] : null;
}

if (process.platform !== 'darwin') {
  console.error('✗ The desktop build is macOS first: run it on a Mac.');
  process.exit(2);
}
if (!fs.existsSync(path.join(ROOT, 'dist/index.html'))) {
  console.error('✗ dist/index.html is missing: npm run desktop:build builds the web app first; run it that way.');
  process.exit(2);
}

const pkg = prepare();
const icns = icon();
const signer = identity();
const electronVersion = JSON.parse(fs.readFileSync(path.join(ROOT, 'node_modules/electron/package.json'), 'utf8')).version;

const config = {
  appId: 'dev.neurocode.desktop',
  productName: 'NeuroCode',
  copyright: `© ${new Date().getFullYear()} NeuroSynth Labs`,
  electronVersion,
  // The Electron already in node_modules, so a build needs no download of its own.
  electronDist: path.join(ROOT, 'node_modules/electron/dist'),
  directories: { app: path.join(DESKTOP, 'app'), output: path.join(DESKTOP, 'dist'), buildResources: BUILD },
  // Only the shell's own files. The app names no dependencies, but electron-builder then looks for node_modules in
  // the project above it and would pack the web app's (React, CodeMirror…, 100 MB) — already bundled in dist/.
  files: ['out/**', 'splash.html', 'splash.js', 'package.json', 'build.json', '!node_modules/**', '!**/node_modules/**'],
  // Nothing is published from here, so no update feed is written into the app.
  publish: null,
  // The Electron copied from node_modules still holds its own sample app, which a packed app never runs.
  afterPack: async (context) => {
    fs.rmSync(path.join(context.appOutDir, 'NeuroCode.app/Contents/Resources/default_app.asar'), { force: true });
  },
  // The web app, served by the desktop's own server from Resources/web.
  extraResources: [{ from: path.join(ROOT, 'dist'), to: 'web' }],
  asar: true,
  npmRebuild: false,
  nodeGypRebuild: false,
  protocols: [{ name: 'NeuroCode', schemes: ['neurocode'] }],
  mac: {
    target: [{ target: 'dmg', arch: ['arm64'] }],
    icon: icns,
    category: 'public.app-category.developer-tools',
    darkModeSupport: true,
    hardenedRuntime: !!signer,
    identity: signer ?? '-',
    notarize: !!(signer && process.env.APPLE_ID && process.env.APPLE_APP_SPECIFIC_PASSWORD && process.env.APPLE_TEAM_ID),
    extendInfo: {
      // What the Dock icon accepts when something is dropped on it: a folder or a text file opens in the Workbench.
      // 'Alternate', so NeuroCode is offered in "Open With" and never becomes the default for either.
      CFBundleDocumentTypes: [
        { CFBundleTypeName: 'Folder', CFBundleTypeRole: 'Viewer', LSHandlerRank: 'Alternate', LSItemContentTypes: ['public.folder'] },
        { CFBundleTypeName: 'Source file', CFBundleTypeRole: 'Editor', LSHandlerRank: 'Alternate', LSItemContentTypes: ['public.text', 'public.source-code'] },
      ],
      NSDesktopFolderUsageDescription: 'NeuroCode opens the project folders you choose on your Desktop.',
      NSDocumentsFolderUsageDescription: 'NeuroCode opens the project folders you choose in Documents.',
      NSDownloadsFolderUsageDescription: 'NeuroCode opens archives and folders you choose in Downloads.',
    },
  },
  dmg: {
    title: 'NeuroCode ${version}',
    artifactName: 'NeuroCode-${version}-${arch}.dmg',
    contents: [{ x: 150, y: 190 }, { x: 390, y: 190, type: 'link', path: '/Applications' }],
    window: { width: 540, height: 380 },
  },
};

const { Arch, Platform, build } = await import('electron-builder');
const made = await build({ targets: Platform.MAC.createTarget(['dmg'], Arch.arm64), config, projectDir: ROOT });
const dmg = made.find((f) => f.endsWith('.dmg'));
console.log(`\n✓ NeuroCode ${pkg.version} for Apple silicon: ${dmg ?? made.join(', ')}`);
console.log(signer
  ? `  Signed with ${signer}${config.mac.notarize ? ' and notarised' : ' — not notarised (set APPLE_ID, APPLE_APP_SPECIFIC_PASSWORD, APPLE_TEAM_ID to notarise)'}.`
  : '  UNSIGNED: no Developer ID identity is in this keychain, so the app is only ad-hoc signed. It runs on this Mac;\n'
    + '  on another Mac, open it with right-click → Open the first time (Gatekeeper does not know it).');
