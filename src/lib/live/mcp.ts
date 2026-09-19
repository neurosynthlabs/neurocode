import { request } from '@/lib/api';
import type { McpServer } from '@/types';

/* The MCP screen's actions beyond registering: trust a server, and check it. A check connects once and
   records what really happened; a failed check still answers 200 with the server's status and reason.
   Checking a stdio server launches its command, so the server refuses it until the server is trusted. */

const seg = encodeURIComponent;

export const mcpApi = {
  /** A check waits on the server, up to ten seconds. */
  check: (id: string) => request<McpServer>(`/mcp/servers/${seg(id)}/check`, { method: 'POST', signal: AbortSignal.timeout(20_000) }),
  trust: (id: string, trusted: boolean) => request<McpServer>(`/mcp/servers/${seg(id)}/trust`, { method: 'POST', json: { trusted } }),
};

/** What launching a stdio server's command needs on top of mcp:manage; the server checks it too. */
export const LAUNCH_PERMISSION = 'workspace:admin';
