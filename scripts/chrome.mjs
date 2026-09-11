// Finds the headless Chromium that playwright-core drives. Shared by smoke, layout-lint and e2e.
// Install it once with:  npx playwright install chromium-headless-shell
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export function findChrome() {
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  const roots = [
    path.join(os.homedir(), 'Library/Caches/ms-playwright'),
    path.join(os.homedir(), '.cache/ms-playwright'),
  ];
  for (const root of roots) {
    if (!fs.existsSync(root)) continue;
    const dirs = fs.readdirSync(root).filter((d) => d.startsWith('chromium_headless_shell-')).sort().reverse();
    for (const dir of dirs) {
      // the install dir also holds marker files such as DEPENDENCIES_VALIDATED, so look for the binary itself
      for (const sub of fs.readdirSync(path.join(root, dir))) {
        for (const bin of ['chrome-headless-shell', 'headless_shell']) {
          const exe = path.join(root, dir, sub, bin);
          if (fs.existsSync(exe)) return exe;
        }
      }
    }
  }
  // Nothing cached under a known layout: let playwright-core launch the browser it installed itself,
  // which is what CI does. If that is missing too, its error names the install command.
  return undefined;
}
