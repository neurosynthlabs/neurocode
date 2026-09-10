import type { AcpClient } from '@/types';

export const acpClients: AcpClient[] = [
  {
    id: 'zed', name: 'Zed', editor: 'Zed', version: '0.214.3',
    status: 'connected', sessionId: 'sess_01JQ7X4M2K9ZB3N',
    capabilities: ['fs.readTextFile', 'fs.writeTextFile', 'terminal', 'session.loadSession', 'prompt.image'],
    permissionMode: 'acceptEdits', lastPing: '2s ago', messages: 1842,
  },
  {
    id: 'zed-nvim', name: 'zed.nvim', editor: 'Neovim', version: '0.11.2 / zed.nvim 0.4.1',
    status: 'connected', sessionId: 'sess_01JQ7WQ8H4TCPD1',
    capabilities: ['fs.readTextFile', 'fs.writeTextFile', 'terminal'],
    permissionMode: 'default', lastPing: '9s ago', messages: 613,
  },
  {
    id: 'vscode', name: 'acp-bridge', editor: 'VS Code', version: '1.106.0 / bridge 0.9.4',
    status: 'connected', sessionId: 'sess_01JQ7V1D6RWK0F8',
    capabilities: ['fs.readTextFile', 'fs.writeTextFile', 'terminal', 'session.loadSession', 'prompt.image', 'prompt.audio'],
    permissionMode: 'plan', lastPing: '41s ago', messages: 2270,
  },
  {
    id: 'jetbrains', name: 'aios-acp', editor: 'JetBrains Rider', version: '2026.2.1 / plugin 0.6.0',
    status: 'idle', sessionId: 'sess_01JQ7T9F0YXM5A2',
    capabilities: ['fs.readTextFile', 'fs.writeTextFile', 'session.loadSession'],
    permissionMode: 'default', lastPing: '12 min ago', messages: 488,
  },
  {
    id: 'cli', name: 'aios acp --stdio', editor: 'CLI client', version: 'aios 0.9.7',
    status: 'connected', sessionId: 'sess_01JQ7XB5N7QJ4E6',
    capabilities: ['fs.readTextFile', 'terminal'],
    permissionMode: 'bypass', lastPing: '1s ago', messages: 5104,
  },
  {
    id: 'emacs', name: 'acp.el', editor: 'Emacs', version: '30.1 / acp.el 0.3.0',
    status: 'disconnected', sessionId: 'sess_01JQ7R3C8VLH9K4',
    capabilities: ['fs.readTextFile', 'fs.writeTextFile'],
    permissionMode: 'default', lastPing: '3 h ago', messages: 96,
  },
];

export interface AcpCapability {
  name: string;
  side: 'client' | 'agent';
  description: string;
}

export const acpCapabilities: AcpCapability[] = [
  { name: 'fs.readTextFile', side: 'client', description: 'OS asks the editor for buffer contents — unsaved edits included' },
  { name: 'fs.writeTextFile', side: 'client', description: 'OS writes through the editor so undo history stays intact' },
  { name: 'terminal', side: 'client', description: 'Agent runs dotnet test / npm run build in the editor terminal' },
  { name: 'session.loadSession', side: 'agent', description: 'Replay a prior session so the editor can rehydrate the thread' },
  { name: 'prompt.image', side: 'agent', description: 'Pasted screenshots reach the Vision agent as image blocks' },
  { name: 'prompt.audio', side: 'agent', description: 'Voice requirement in Hinglish, transcribed before compiling' },
];

/** Which client advertises which capability, keyed clientId → capability names. */
export const capabilityMatrix: Record<string, Record<string, 'yes' | 'no' | 'partial'>> = {
  zed: { 'fs.readTextFile': 'yes', 'fs.writeTextFile': 'yes', terminal: 'yes', 'session.loadSession': 'yes', 'prompt.image': 'yes', 'prompt.audio': 'no' },
  'zed-nvim': { 'fs.readTextFile': 'yes', 'fs.writeTextFile': 'yes', terminal: 'yes', 'session.loadSession': 'no', 'prompt.image': 'no', 'prompt.audio': 'no' },
  vscode: { 'fs.readTextFile': 'yes', 'fs.writeTextFile': 'yes', terminal: 'yes', 'session.loadSession': 'yes', 'prompt.image': 'yes', 'prompt.audio': 'partial' },
  jetbrains: { 'fs.readTextFile': 'yes', 'fs.writeTextFile': 'yes', terminal: 'partial', 'session.loadSession': 'yes', 'prompt.image': 'no', 'prompt.audio': 'no' },
  cli: { 'fs.readTextFile': 'yes', 'fs.writeTextFile': 'no', terminal: 'yes', 'session.loadSession': 'no', 'prompt.image': 'no', 'prompt.audio': 'no' },
  emacs: { 'fs.readTextFile': 'yes', 'fs.writeTextFile': 'yes', terminal: 'no', 'session.loadSession': 'no', 'prompt.image': 'no', 'prompt.audio': 'no' },
};

export interface LifecycleStep {
  method: string;
  from: 'client' | 'agent';
  note: string;
  ms: number;
}

export const lifecycle: LifecycleStep[] = [
  { method: 'initialize', from: 'client', note: 'protocolVersion 1, client announces fs + terminal', ms: 6 },
  { method: 'authenticate', from: 'client', note: 'methodId "aios-local" — no token, loopback socket only', ms: 3 },
  { method: 'session/new', from: 'client', note: 'cwd /Users/rajat/work/careworks-erp, 16 MCP servers attached', ms: 214 },
  { method: 'session/prompt', from: 'client', note: 'Hinglish requirement + 2 file mentions from the editor', ms: 1 },
  { method: 'session/update', from: 'agent', note: 'streamed: agent_message_chunk, tool_call, plan (48 frames)', ms: 9420 },
  { method: 'session/request_permission', from: 'agent', note: 'write TaxService.cs — answered by permission mode', ms: 2 },
  { method: 'session/cancel', from: 'client', note: 'Esc in the editor — turn ends with stopReason "cancelled"', ms: 4 },
];

export const permissionModes = [
  {
    id: 'default', label: 'default',
    allows: 'Every file write and every terminal command raises session/request_permission in the editor.',
    blocks: 'Nothing runs unattended. Slowest, safest — this is what the ERP repo runs on.',
  },
  {
    id: 'acceptEdits', label: 'acceptEdits',
    allows: 'File edits inside the active worktree apply without asking. Reads are always free.',
    blocks: 'Terminal commands, git push, and anything outside the worktree still prompt.',
  },
  {
    id: 'plan', label: 'plan',
    allows: 'Read, search and analyse only. The agent produces a plan and stops at the edit boundary.',
    blocks: 'All writes, all terminal, all MCP tools marked HIGH or CRITICAL.',
  },
  {
    id: 'bypass', label: 'bypass',
    allows: 'No prompts at all. Only for the sandboxed CLI client on a throwaway container.',
    blocks: 'Nothing — the OS refuses to enable it on any client whose cwd is a real repo.',
  },
];

export const jsonRpcTail = `→ {"jsonrpc":"2.0","id":41,"method":"session/prompt","params":{
     "sessionId":"sess_01JQ7X4M2K9ZB3N",
     "prompt":[{"type":"text","text":"invoice tax interstate pe ulta aa raha hai, dekho"},
               {"type":"resource_link","uri":"file:///Users/rajat/work/careworks-erp/src/Services/TaxService.cs"}]}}

← {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"sess_01JQ7X4M2K9ZB3N",
     "update":{"sessionUpdate":"agent_message_chunk",
               "content":{"type":"text","text":"Reading TaxService.ResolveJurisdiction…"}}}}

← {"jsonrpc":"2.0","id":"c_18","method":"fs/read_text_file","params":{
     "sessionId":"sess_01JQ7X4M2K9ZB3N",
     "path":"/Users/rajat/work/careworks-erp/src/Services/TaxService.cs","line":118,"limit":142}}
→ {"jsonrpc":"2.0","id":"c_18","result":{"content":"public TaxJurisdiction ResolveJurisdiction(…"}}

← {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"sess_01JQ7X4M2K9ZB3N",
     "update":{"sessionUpdate":"tool_call","toolCallId":"tc_09","status":"in_progress",
               "title":"sqlserver · get_stored_procedure_definition","kind":"read",
               "rawInput":{"name":"SP_CalculateTax"}}}}

← {"jsonrpc":"2.0","id":"c_19","method":"session/request_permission","params":{
     "sessionId":"sess_01JQ7X4M2K9ZB3N","toolCall":{"toolCallId":"tc_11",
     "title":"Edit src/Services/TaxService.cs (+18 −6)"},
     "options":[{"optionId":"allow_once","name":"Allow","kind":"allow_once"},
                {"optionId":"allow_always","name":"Allow for session","kind":"allow_always"},
                {"optionId":"reject","name":"Reject","kind":"reject_once"}]}}
→ {"jsonrpc":"2.0","id":"c_19","result":{"outcome":{"outcome":"selected","optionId":"allow_once"}}}
     ⤷ auto-answered by permissionMode=acceptEdits (worktree task/invoice-tax-backend)

← {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"sess_01JQ7X4M2K9ZB3N",
     "update":{"sessionUpdate":"tool_call_update","toolCallId":"tc_11","status":"completed",
               "content":[{"type":"diff","path":".../TaxService.cs","oldText":"…","newText":"…"}]}}}

← {"jsonrpc":"2.0","id":41,"result":{"stopReason":"end_turn"}}`;

export interface AcpEvent {
  at: string;
  client: string;
  method: string;
  dir: 'in' | 'out';
  detail: string;
}

export const acpEvents: AcpEvent[] = [
  { at: '11:42:11', client: 'Zed', method: 'session/update', dir: 'out', detail: 'tool_call_update tc_11 completed — diff applied' },
  { at: '11:42:09', client: 'Zed', method: 'session/request_permission', dir: 'out', detail: 'auto-allowed by acceptEdits' },
  { at: '11:42:02', client: 'Zed', method: 'fs/read_text_file', dir: 'out', detail: 'TaxService.cs:118 +142 lines' },
  { at: '11:41:55', client: 'CLI client', method: 'terminal/create', dir: 'out', detail: 'dotnet test --filter TaxTests' },
  { at: '11:41:48', client: 'VS Code', method: 'session/prompt', dir: 'in', detail: 'plan mode — "MST_TAX ka blast radius batao"' },
  { at: '11:41:30', client: 'Zed', method: 'session/prompt', dir: 'in', detail: 'text + resource_link TaxService.cs' },
  { at: '11:41:12', client: 'Neovim', method: 'session/new', dir: 'in', detail: 'cwd careworks-erp, 16 MCP servers attached' },
  { at: '11:40:58', client: 'VS Code', method: 'session/update', dir: 'out', detail: 'plan: 6 steps, 0 edits (plan mode)' },
  { at: '11:40:31', client: 'CLI client', method: 'terminal/output', dir: 'out', detail: '18 passed, 0 failed in 6.4s' },
  { at: '11:40:04', client: 'Rider', method: 'session/cancel', dir: 'in', detail: 'stopReason cancelled after 21s' },
  { at: '11:39:37', client: 'Zed', method: 'session/update', dir: 'out', detail: 'agent_message_chunk ×34' },
  { at: '11:39:02', client: 'VS Code', method: 'session/load', dir: 'in', detail: 'replayed sess_01JQ7V1D6RWK0F8, 212 frames' },
  { at: '11:38:44', client: 'Emacs', method: 'initialize', dir: 'in', detail: 'refused — protocolVersion 0 unsupported' },
  { at: '11:38:20', client: 'Zed', method: 'fs/write_text_file', dir: 'out', detail: 'TaxService.cs written through editor buffer' },
];
