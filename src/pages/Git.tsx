import { useMemo, useState } from 'react';
import { GitMerge, GitPullRequest, TriangleAlert, Check, Search } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, Ascii, ListRow, Segmented, Field,
  DataTable, Row, Cell, Stat, StatGrid, KV, Empty, Toolbar, SectionTitle,
} from '@/components/os';
import { worktreeTree, worktrees, changedFiles, mergePreview, conflicts, commits, pullRequests } from '@/mock/git';
import { agentName } from '@/mock/agents';

const PR_TONE = { draft: 'neutral', open: 'info', awaiting_human: 'warn', merged: 'ok', blocked: 'danger' } as const;
const CHK_TONE = { pass: 'ok', fail: 'danger', running: 'info', skipped: 'neutral', warn: 'warn' } as const;

export default function Git() {
  const [tab, setTab] = useState<'worktrees' | 'conflicts' | 'commits' | 'prs'>('worktrees');
  const [sel, setSel] = useState(worktrees[0].id);
  const [q, setQ] = useState('');
  const wt = useMemo(() => worktrees.find((w) => w.id === sel) ?? worktrees[0], [sel]);
  const files = changedFiles[wt.id] ?? [];
  const cmts = useMemo(() => {
    const s = q.trim().toLowerCase();
    return s ? commits.filter((c) => (c.message + c.author + c.sha + c.branch).toLowerCase().includes(s)) : commits;
  }, [q]);

  return (
    <Page>
      <PageHeader
        title="Git & Worktrees"
        subtitle="Every agent gets its own worktree. Nothing they do can destroy another agent's work — collisions surface at merge, not mid-edit."
        actions={<Segmented
          options={[
            { id: 'worktrees', label: `Worktrees (${worktrees.length})` },
            { id: 'conflicts', label: `Conflicts (${conflicts.length})` },
            { id: 'commits', label: `Commits (${commits.length})` },
            { id: 'prs', label: `PRs (${pullRequests.length})` },
          ]}
          value={tab} onChange={setTab} />}
      />

      <PageBody className="space-y-4">
        <StatGrid cols={5}>
          <Stat label="Worktrees" value={worktrees.length} sub={`${worktrees.filter((w) => w.status === 'dirty').length} dirty`} />
          <Stat label="Clean merges" value={mergePreview.filter((m) => m.result === 'clean').length} tone="ok" sub="ready to land" />
          <Stat label="Collisions" value={mergePreview.filter((m) => m.result === 'collides').length} tone="danger" sub="need a resolution" />
          <Stat label="Commits today" value={commits.length} sub="all by agents" />
          <Stat label="Awaiting you" value={pullRequests.filter((p) => p.state === 'awaiting_human').length} tone="warn" sub="final merge gate" />
        </StatGrid>

        {tab === 'worktrees' && (
          <>
            <Panel eyebrow="One branch per agent per task" title="Worktree layout">
              <Ascii className="overflow-auto">{worktreeTree}</Ascii>
            </Panel>

            <div className="flex min-h-[420px] gap-3">
              <div className="no-scrollbar w-[320px] shrink-0 overflow-y-auto rounded-md border border-line bg-surface">
                {worktrees.map((w) => (
                  <ListRow key={w.id} active={w.id === sel} onClick={() => setSel(w.id)}>
                    <div className="flex items-center gap-2">
                      <Dot state={w.status} pulse={w.status === 'dirty'} />
                      <Mono className="truncate">{w.branch}</Mono>
                    </div>
                    <div className="mt-1 flex items-center gap-2 text-[10.5px] text-dim">
                      <span className="truncate">{agentName(w.agent)}</span>
                      <span className="ml-auto text-ok">+{w.additions}</span>
                      <span className="text-danger">−{w.deletions}</span>
                    </div>
                    <div className="mt-0.5 flex items-center gap-2 text-[10px] text-dim">
                      <span>{w.taskRef}</span>
                      <span className="ml-auto">{w.filesChanged} files · {w.lastCommitAt}</span>
                    </div>
                  </ListRow>
                ))}
              </div>

              <div className="min-w-0 flex-1 space-y-3">
                <Panel eyebrow={wt.taskRef} title={<span className="flex items-center gap-2"><Mono tone="brand">{wt.branch}</Mono><Tag tone={wt.status === 'conflict' ? 'danger' : wt.status === 'dirty' ? 'warn' : 'ok'}>{wt.status}</Tag></span>}
                  actions={<div className="flex gap-1.5">
                    <Button size="xs" variant="outline" onClick={() => toast(`Comparing ${wt.branch} against main`)}>Compare</Button>
                    <Button size="xs" variant="outline" onClick={() => toast(`${wt.branch} merged into main`)}><GitMerge className="size-3" />Merge</Button>
                    <Button size="xs" onClick={() => toast('PR opened — reviewer assigned')}><GitPullRequest className="size-3" />Create PR</Button>
                  </div>}>
                  <div className="grid grid-cols-2 gap-x-6 md:grid-cols-4">
                    <KV k="Owner" v={agentName(wt.agent)} />
                    <KV k="Path" v={wt.path} mono />
                    <KV k="Ahead / behind" v={`${wt.ahead} / ${wt.behind}`} />
                    <KV k="Last commit" v={wt.lastCommit} mono />
                  </div>
                </Panel>

                <Panel eyebrow={`${files.length} files changed`} title="Diff" flush>
                  {files.length === 0 ? <Empty title="No changes recorded" /> : (
                    <div className="divide-y divide-line">
                      {files.map((f) => (
                        <div key={f.path} className="px-3.5 py-2.5">
                          <div className="flex items-center gap-2">
                            <Tag tone={f.change === 'A' ? 'ok' : f.change === 'D' ? 'danger' : 'warn'}>{f.change}</Tag>
                            <Mono className="truncate">{f.path}</Mono>
                            <span className="ml-auto shrink-0 tnum text-[11px]"><span className="text-ok">+{f.additions}</span> <span className="text-danger">−{f.deletions}</span></span>
                          </div>
                          <pre className="ascii mt-1.5 overflow-x-auto rounded-sm border border-line bg-base p-2.5">
                            {f.hunk.split('\n').map((l, i) => (
                              <div key={i} className={l.startsWith('+') ? 'text-ok' : l.startsWith('-') ? 'text-danger' : l.startsWith('@') ? 'text-brand' : ''}>{l}</div>
                            ))}
                          </pre>
                        </div>
                      ))}
                    </div>
                  )}
                </Panel>

                <Panel eyebrow="Before anything lands" title="Merge preview" flush>
                  <DataTable head={['Worktree', 'Into', 'Result', 'Files', 'Note']}>
                    {mergePreview.map((m) => (
                      <Row key={m.worktreeId}>
                        <Cell mono>{worktrees.find((w) => w.id === m.worktreeId)?.branch ?? m.worktreeId}</Cell>
                        <Cell mono className="text-dim">{m.target}</Cell>
                        <Cell><Tag tone={m.result === 'clean' ? 'ok' : m.result === 'collides' ? 'danger' : 'warn'}>{m.result}</Tag></Cell>
                        <Cell className="tnum">{m.files}</Cell>
                        <Cell className="max-w-[420px] text-[11.5px] text-soft">{m.note}{m.onFile && <Mono className="ml-1">{m.onFile}</Mono>}</Cell>
                      </Row>
                    ))}
                  </DataTable>
                </Panel>
              </div>
            </div>
          </>
        )}

        {tab === 'conflicts' && (
          <div className="space-y-3">
            {conflicts.length === 0 ? <Empty title="No collisions" hint="Every worktree merges cleanly right now." /> : conflicts.map((c) => (
              <Panel key={c.id} className="border-danger/35" eyebrow={`${c.taskRef} · ${c.region}`}
                title={<span className="flex items-center gap-2"><TriangleAlert className="size-3.5 text-danger" /><Mono>{c.file}</Mono></span>}>
                <div className="grid grid-cols-1 gap-2.5 lg:grid-cols-2">
                  {c.hunks.map((h) => (
                    <div key={h.side} className="rounded-sm border border-line bg-base p-3">
                      <div className="mb-1.5 flex items-center gap-2">
                        <Tag tone={h.side === 'ours' ? 'info' : 'violet'}>{h.side}</Tag>
                        <Mono>{h.branch}</Mono>
                        <span className="ml-auto text-[10.5px] text-dim">{agentName(h.agent)} · {h.at}</span>
                      </div>
                      <pre className="ascii overflow-x-auto">{h.lines}</pre>
                    </div>
                  ))}
                </div>
                <SectionTitle className="mt-3 mb-1.5">Orchestrator policy</SectionTitle>
                <ul className="space-y-1">
                  {c.policy.map((p) => (
                    <li key={p} className="flex items-start gap-1.5 text-[11.5px] text-soft"><Check className="mt-px size-3 shrink-0 text-brand" />{p}</li>
                  ))}
                </ul>
                <div className="mt-2.5 flex items-center justify-between gap-3 border-t border-line pt-2.5">
                  <p className="text-[12px] text-ink-2">{c.resolution}</p>
                  <Tag tone="neutral">{c.resolvedBy}</Tag>
                </div>
              </Panel>
            ))}
          </div>
        )}

        {tab === 'commits' && (
          <>
            <Toolbar>
              <Field className="w-72" value={q} onChange={setQ} icon={<Search className="size-3.5" />} placeholder="Search commits, authors, branches…" onClear={() => setQ('')} />
              <span className="ml-auto text-[11.5px] text-dim">{cmts.length} of {commits.length}</span>
            </Toolbar>
            <Panel flush>
              {cmts.length === 0 ? <Empty title="No commit matches" /> : (
                <DataTable head={['SHA', 'Message', 'Author', 'Branch', 'Files', 'When']}>
                  {cmts.map((c) => (
                    <Row key={c.sha}>
                      <Cell mono className="text-brand">{c.sha.slice(0, 7)}</Cell>
                      <Cell className="max-w-[560px] text-ink">{c.message}</Cell>
                      <Cell className="text-[11.5px]">{c.author}</Cell>
                      <Cell mono className="text-dim">{c.branch}</Cell>
                      <Cell className="tnum">{c.files}</Cell>
                      <Cell className="text-dim">{c.at}</Cell>
                    </Row>
                  ))}
                </DataTable>
              )}
            </Panel>
          </>
        )}

        {tab === 'prs' && (
          <div className="space-y-3">
            {pullRequests.map((p) => (
              <Panel key={p.id} eyebrow={`${p.branch} → ${p.base} · ${p.openedAt}`}
                title={<span className="flex flex-wrap items-center gap-2"><Mono tone="brand">{p.number}</Mono>{p.title}<Tag tone={PR_TONE[p.state]}>{p.state.replace('_', ' ')}</Tag></span>}
                actions={<span className="text-[11px] text-dim">{p.files} files · <span className="text-ok">+{p.additions}</span> <span className="text-danger">−{p.deletions}</span> · {p.commits} commits</span>}>
                <p className="max-w-4xl text-[12px] leading-relaxed text-ink-2">{p.body}</p>
                <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">
                  <div>
                    <SectionTitle>Checks</SectionTitle>
                    <div className="divide-y divide-line rounded-sm border border-line">
                      {p.checks.map((ch) => (
                        <div key={ch.id} className="flex items-center gap-2 px-2.5 py-1.5">
                          <Dot state={ch.status} pulse={ch.status === 'running'} />
                          <span className="text-[12px] text-ink-2">{ch.name}</span>
                          <span className="ml-auto truncate text-[11px] text-dim">{ch.detail}</span>
                          <Tag tone={CHK_TONE[ch.status]}>{ch.status}</Tag>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div>
                    <SectionTitle>Gate</SectionTitle>
                    <div className="rounded-sm border border-warn/30 bg-warn/8 p-3">
                      <p className="text-[12px] text-warn">{p.humanGate}</p>
                      <div className="mt-2 flex flex-wrap gap-1">
                        {p.reviewers.map((r) => <Tag key={r} tone="neutral">{r}</Tag>)}
                      </div>
                      {p.state === 'awaiting_human' && (
                        <div className="mt-2.5 flex gap-1.5">
                          <Button size="xs" onClick={() => toast.success(`${p.number} approved and merged`)}>Approve & merge</Button>
                          <Button size="xs" variant="outline" onClick={() => toast(`${p.number} sent back for changes`)}>Request changes</Button>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </Panel>
            ))}
          </div>
        )}
      </PageBody>
    </Page>
  );
}
