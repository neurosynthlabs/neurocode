import { request } from '@/lib/api';
import type { McpServer } from '@/types';
import type { RuleDecision } from '@/lib/live/work';

/* The MCP screen's actions beyond registering: trust a server, check it, and try one of its tools. A
   check connects once and records what really happened; a failed check still answers 200 with the
   server's status and reason. Checking a stdio server launches its command, so the server refuses it
   until the server is trusted — and a tool is only ever called on a trusted server a check found
   connected, once the tool rules allow it. */

const seg = encodeURIComponent;

export const mcpApi = {
  /** A check waits on the server, up to ten seconds. */
  check: (id: string) => request<McpServer>(`/mcp/servers/${seg(id)}/check`, { method: 'POST', signal: AbortSignal.timeout(20_000) }),
  trust: (id: string, trusted: boolean) => request<McpServer>(`/mcp/servers/${seg(id)}/trust`, { method: 'POST', json: { trusted } }),
  /** Calls one tool a check listed. Refused (409/403/404) before anything is sent when it may not run;
      once sent, a failure is still a 200 with `ok: false` and the reason. A call waits up to thirty seconds. */
  call: (id: string, tool: string, args: Record<string, unknown>, projectId: string | null) =>
    request<ToolCall>(`/mcp/servers/${seg(id)}/tools/${seg(tool)}/call`, {
      method: 'POST', json: { arguments: args, projectId }, signal: AbortSignal.timeout(45_000),
    }),
};

/** What one tool call came back with. `isError` is the tool saying its work failed; `error` is the call failing. */
export interface ToolCall {
  server: string;
  tool: string;
  ok: boolean;
  isError: boolean;
  text: string;
  truncated: boolean;
  ms: number | null;
  error: string;
  decision: RuleDecision;
}

/** What launching a stdio server's command needs on top of mcp:manage; the server checks it too. */
export const LAUNCH_PERMISSION = 'workspace:admin';
