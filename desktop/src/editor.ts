/* "Open in editor" and "Reveal in Finder" for a file the web app shows.

   The editor is the person's own: VS Code, Cursor or Windsurf when one is installed (their command-line tool
   opens a file at a line with `-g file:line`), else NEUROCODE_EDITOR when set, else macOS's default editor
   for text (`open -t`), which has no way to be told a line. Never plain `open`: that runs whatever the file
   type is bound to, and a .command or .app would be executed rather than read. */
import { execFile } from 'node:child_process';
import { existsSync } from 'node:fs';
import { stat } from 'node:fs/promises';
import path from 'node:path';
import { adoptShellPath, which } from './stack.js';

interface Editor { name: string; command: string; lines: boolean }

const KNOWN: { name: string; program: string; bundled: string }[] = [
  { name: 'Visual Studio Code', program: 'code', bundled: '/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code' },
  { name: 'Cursor', program: 'cursor', bundled: '/Applications/Cursor.app/Contents/Resources/app/bin/cursor' },
  { name: 'Windsurf', program: 'windsurf', bundled: '/Applications/Windsurf.app/Contents/Resources/app/bin/windsurf' },
];

let found: Promise<Editor> | null = null;

/** The editor files open in, found once per launch. */
export function editor(): Promise<Editor> {
  found ??= (async () => {
    await adoptShellPath();
    const chosen = process.env.NEUROCODE_EDITOR?.trim();
    if (chosen) {
      const command = path.isAbsolute(chosen) ? chosen : which(chosen);
      if (command) return { name: path.basename(command), command, lines: true };
      console.warn(`[NeuroCode] NEUROCODE_EDITOR names ${chosen}, which was not found; looking for a known editor instead.`);
    }
    for (const k of KNOWN) {
      const command = which(k.program) ?? (existsSync(k.bundled) ? k.bundled : null);
      if (command) return { name: k.name, command, lines: true };
    }
    return { name: 'the default text editor', command: '/usr/bin/open', lines: false };
  })();
  return found;
}

/** An absolute path that is there: a file or a folder, never a device or a socket. */
async function real(target: unknown): Promise<string> {
  if (typeof target !== 'string' || !path.isAbsolute(target) || target.includes('\0')) {
    throw new Error('Only an absolute path on this Mac can be opened.');
  }
  const info = await stat(target).catch(() => null);
  if (!info) throw new Error(`${target} is not on this Mac any more.`);
  if (!info.isFile() && !info.isDirectory()) throw new Error(`${target} is neither a file nor a folder.`);
  return path.normalize(target);
}

function exec(command: string, args: string[]): Promise<void> {
  return new Promise((resolve, reject) => {
    execFile(command, args, { timeout: 15_000, env: process.env }, (err, _out, errText) => {
      if (err) reject(new Error((errText || err.message).trim().split('\n')[0]));
      else resolve();
    });
  });
}

/** Open a file (at a line, when the editor can) or a folder in the person's editor. Returns the editor's name. */
export async function openInEditor(target: unknown, line?: unknown): Promise<string> {
  const file = await real(target);
  const ed = await editor();
  const at = typeof line === 'number' && Number.isInteger(line) && line > 0 ? line : null;
  if (!ed.lines) {
    if ((await stat(file)).isDirectory()) {
      throw new Error('No code editor was found on this Mac (VS Code, Cursor or Windsurf, or NEUROCODE_EDITOR), and a folder cannot be opened in a text editor.');
    }
    await exec(ed.command, ['-t', file]);
    return ed.name;
  }
  await exec(ed.command, at ? ['-g', `${file}:${at}`] : [file]);
  return ed.name;
}

/** The path that reveal would show: checked the same way, so a menu is only offered for something that is there. */
export const checkedPath = real;
