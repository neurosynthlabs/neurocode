import { useMemo, useState } from 'react';
import { History, RotateCcw, GitFork, Search, TriangleAlert, Archive, User, Crown } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, ListRow, Field, KV,
  Stat, StatGrid, Empty, SectionTitle,
} from '@/components/os';
import { sessions, transcriptDemo, compaction } from '@/mock/workflows';
import { projectName } from '@/mock/projects';
import { cn } from '@/lib/utils';
import type { Checkpoint } from '@/types';

export default function Sessions() {
  const [q, setQ] = useState('');
  const [sel, setSel] = useState(sessions[0].id);
  const [restore, setRestore] = useState<Checkpoint | null>(null);

  const list = useMemo(() => {
    const t = q.trim().toLowerCase();
    return t ? sessions.filter((s) => (s.ref + s.title + s.summary + s.agents.join(' ')).toLowerCase().includes(t)) : sessions;
  }, [q]);
  const s = useMemo(() => list.find((x) => x.id === sel) ?? list[0] ?? sessions[0], [list, sel]);

  return (
    <Page>
      <PageHeader
        title="Sessions"
        subtitle="Every session keeps checkpoints. Rewind files and conversation to any of them, or fork a new line of work without losing the original."
      />

      <PageBody className="space-y-4">
        <StatGrid cols={5}>
          <Stat label="Sessions" value={sessions.length} icon={<History className="size-3" />} />
          <Stat label="Active" value={sessions.filter((x) => x.status === 'active').length} tone="ok" />
          <Stat label="Checkpoints" value={sessions.reduce((n, x) => n + x.checkpoints.length, 0)} tone="brand" sub="all restorable" />
          <Stat label="Tokens" value={`${(sessions.reduce((n, x) => n + x.tokens, 0) / 1e6).toFixed(1)}M`} />
          <Stat label="Cost" value={`$${sessions.reduce((n, x) => n + x.cost, 0).toFixed(2)}`} sub="local models carry most of it" />
        </StatGrid>

        <div className="flex min-h-[620px] flex-col gap-3 md:flex-row">
          <div className="flex w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[320px] flex-col overflow-hidden rounded-md border border-line bg-surface">
            <div className="border-b border-line p-2.5">
              <Field value={q} onChange={setQ} icon={<Search className="size-3.5" />} placeholder="Search sessions…" onClear={() => setQ('')} />
            </div>
            <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto">
              {list.length === 0 ? <Empty title="No session matches" /> : list.map((x) => (
                <ListRow key={x.id} active={x.id === s.id} onClick={() => setSel(x.id)}>
                  <div className="flex items-center gap-2">
                    <Dot state={x.status} pulse={x.status === 'active'} />
                    <Mono>{x.ref}</Mono>
                    <span className="eyebrow ml-auto">{x.status}</span>
                  </div>
                  <p className="mt-1 truncate text-[13px] font-medium text-ink">{x.title}</p>
                  <p className="mt-0.5 truncate text-[11.5px] text-dim">{projectName(x.projectId)} · {x.startedAt} · {x.duration}</p>
                </ListRow>
              ))}
            </div>
          </div>

          <div className="min-w-0 flex-1 space-y-3">
            <Panel eyebrow={`${projectName(s.projectId)} · started ${s.startedAt}`}
              title={<span className="flex items-center gap-2"><Mono tone="brand">{s.ref}</Mono>{s.title}</span>}
              actions={<Button size="xs" variant="outline" onClick={() => toast.success(`Forked ${s.ref}`, { description: 'New session from the latest checkpoint; the original is untouched.' })}><GitFork className="size-3" />Fork</Button>}>
              <p className="text-[13.5px] leading-relaxed text-ink-2">{s.summary}</p>
              <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-x-6 border-t border-line pt-2.5 xl:grid-cols-4">
                <KV k="Duration" v={s.duration} />
                <KV k="Messages" v={s.messages} />
                <KV k="Tokens" v={`${(s.tokens / 1000).toFixed(0)}k`} />
                <KV k="Cost" v={s.cost === 0 ? 'free' : `$${s.cost.toFixed(2)}`} />
              </div>
              <div className="mt-2 flex flex-wrap gap-1">{s.agents.map((a) => <Tag key={a} tone="neutral">{a}</Tag>)}</div>
            </Panel>

            <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
              <Panel eyebrow="Rewind to any point" title={`Checkpoints · ${s.checkpoints.length}`} flush>
                <div className="relative py-1">
                  <span className="absolute top-3 bottom-3 left-[21px] w-px bg-line" />
                  {s.checkpoints.map((c, i) => (
                    <div key={c.id} className="relative flex items-center gap-3 px-3.5 py-2">
                      <span className={cn('relative z-10 size-2.5 shrink-0 rounded-full border-2',
                        i === s.checkpoints.length - 1 ? 'border-brand bg-brand' : 'border-line-strong bg-surface')} />
                      <span className="min-w-0 flex-1">
                        <span className="block text-[13px] text-ink">{c.label}</span>
                        <span className="block text-[11.5px] text-dim">{c.at} · {c.files} files · {(c.tokens / 1000).toFixed(0)}k tokens</span>
                      </span>
                      {i < s.checkpoints.length - 1 && (
                        <Button size="xs" variant="ghost" onClick={() => setRestore(c)}><RotateCcw className="size-3" />Restore</Button>
                      )}
                    </div>
                  ))}
                </div>
              </Panel>

              <Panel eyebrow="Context was summarised here" title={<span className="flex items-center gap-1.5"><Archive className="size-3.5 text-brand" />Compaction · {compaction.at}</span>}>
                <div className="flex items-center gap-3">
                  <span className="tnum font-mono text-[13px] text-soft">{(compaction.before / 1000).toFixed(0)}k</span>
                  <span className="text-dim">→</span>
                  <span className="tnum font-mono text-[13px] text-ok">{(compaction.after / 1000).toFixed(0)}k tokens</span>
                </div>
                <SectionTitle className="mt-3 mb-1.5">Preserved in memory</SectionTitle>
                <ul className="space-y-1">
                  {compaction.preserved.map((p) => <li key={p} className="flex gap-1.5 text-[12.5px] text-ink-2"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-ok" />{p}</li>)}
                </ul>
                <p className="mt-2.5 border-t border-line pt-2 text-[12px] text-dim">{compaction.note}</p>
              </Panel>
            </div>

            <Panel eyebrow="Transcript preview" title="Conversation" flush>
              <div className="space-y-0 divide-y divide-line">
                {transcriptDemo.map((m, i) => (
                  <div key={i} className={cn('flex gap-3 px-3.5 py-2.5', m.role === 'you' && 'bg-brand/5')}>
                    <span className={cn('grid size-6 shrink-0 place-items-center rounded-sm',
                      m.role === 'you' ? 'bg-brand/15 text-brand' : 'bg-surface-3 text-violet')}>
                      {m.role === 'you' ? <User className="size-3.5" /> : <Crown className="size-3.5" />}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-2">
                        <span className="text-[12.5px] font-semibold text-ink">{m.role === 'you' ? 'You' : 'AI Commander'}</span>
                        <span className="font-mono text-[11.5px] text-dim">{m.at}</span>
                      </span>
                      <span className="mt-0.5 block text-[13.5px] leading-relaxed text-ink-2">{m.text}</span>
                    </span>
                  </div>
                ))}
              </div>
            </Panel>
          </div>
        </div>
      </PageBody>

      <Dialog open={!!restore} onOpenChange={(o) => !o && setRestore(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2"><TriangleAlert className="size-4 text-warn" />Restore “{restore?.label}”?</DialogTitle>
            <DialogDescription>
              This rewinds <span className="text-ink">both the files and the conversation</span> to {restore?.at}. Everything after
              that point in {s.ref} is set aside — recoverable by forking, but no longer the active line of work.
            </DialogDescription>
          </DialogHeader>
          <div className="rounded-sm border border-line bg-base px-3 py-1.5">
            <KV k="Files reverted" v={restore?.files ?? 0} />
            <KV k="Context at checkpoint" v={`${((restore?.tokens ?? 0) / 1000).toFixed(0)}k tokens`} />
            <KV k="Memory" v="untouched — facts learned after this point are kept" />
          </div>
          <DialogFooter>
            <Button size="sm" variant="outline" onClick={() => setRestore(null)}>Cancel</Button>
            <Button size="sm" variant="destructive" onClick={() => { toast.success(`Restored to “${restore?.label}”`); setRestore(null); }}>
              <RotateCcw className="size-3.5" />Restore
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Page>
  );
}
