// Run by Electron (`electron desktop/scripts/icon.mjs <out.png>`): draws public/neurocode.svg — the marigold mark on
// its rounded square — onto a 1024 px canvas the way macOS icons sit (an 824 px body, centred, with the soft shadow
// the Dock expects), and writes it as a PNG. Chromium does the drawing, so the SVG needs no other rasteriser.
import { BrowserWindow, app } from 'electron';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const out = process.argv[process.argv.length - 1];
if (!out.endsWith('.png')) {
  console.error('usage: electron desktop/scripts/icon.mjs <out.png>');
  process.exit(2);
}
const svg = fs.readFileSync(path.join(ROOT, 'public/neurocode.svg'), 'utf8');
const html = `<!doctype html><html><body style="margin:0;background:transparent">
<div style="width:1024px;height:1024px;display:grid;place-items:center">
<img src="data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}"
  style="width:824px;height:824px;filter:drop-shadow(0 12px 22px rgba(0,0,0,.28))"></div></body></html>`;

// Not a top-level await: Electron fires 'ready' only after the main module has finished evaluating.
app.dock?.hide();
void app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1024, height: 1024, show: false, frame: false, transparent: true, useContentSize: true,
    webPreferences: { offscreen: true, sandbox: true, contextIsolation: true },
  });
  await win.loadURL(`data:text/html;base64,${Buffer.from(html).toString('base64')}`);
  await new Promise((r) => setTimeout(r, 300));
  const image = await win.webContents.capturePage({ x: 0, y: 0, width: 1024, height: 1024 });
  const png = image.resize({ width: 1024, height: 1024, quality: 'best' }).toPNG();
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, png);
  console.log(`✓ ${out} · ${image.getSize().width}×${image.getSize().height}`);
  app.quit();
}).catch((e) => {
  console.error('✗ the icon was not drawn:', e);
  app.exit(1);
});
