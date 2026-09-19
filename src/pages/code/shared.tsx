import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { FolderGit2, Target, TriangleAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Empty, Mono, Page, PageBody, PageHeader, Panel, RiskPill, Ring } from '@/components/os';
import type { Impact } from '@/lib/api';
import { cn } from '@/lib/utils';
import { baseName } from './format';

/** A screen that reads one project, in a workspace that has none yet: say what fills it, and go there. */
export function NoProject({ title, hint }: { title: string; hint: string }) {
  const nav = useNavigate();
  return (
    <Page>
      <PageHeader title={title} />
      <PageBody>
        <Empty icon={<FolderGit2 className="size-6" />} title="No project yet" hint={hint}
          action={<Button size="sm" onClick={() => nav('/projects')}>Onboard a repository</Button>} />
      </PageBody>
    </Page>
  );
}

/** "If this changes…": the blast radius of a file, a module or a database object, measured on the graph. */
export function ImpactPanel({ impact, onOpen, actions }: { impact: Impact; onOpen?: (path: string) => void; actions?: ReactNode }) {
  const hot = impact.risk === 'HIGH' || impact.risk === 'CRITICAL';
  return (
    <div className="space-y-3">
      <Panel
        className={cn('accent-top', hot ? 'border-danger/40' : impact.risk === 'MEDIUM' ? 'border-warn/35' : '')}
        eyebrow="Impact · measured on the dependency graph"
        title={<span className="flex items-center gap-2"><Target className="size-3.5 shrink-0 text-brand" />If {baseName(impact.target)} changes…</span>}
        actions={<RiskPill risk={impact.risk} />}
      >
        <div className="flex items-center gap-4">
          <Ring pct={impact.confidence} size={54} />
          <div className="grid flex-1 grid-cols-2 gap-3 sm:grid-cols-4">
            {([
              [impact.counts.direct, 'use it directly'],
              [impact.counts.dependents, 'reached in all'],
              [impact.counts.modules, 'modules'],
              [impact.counts.tests, 'tests'],
            ] as const).map(([v, label]) => (
              <div key={label}>
                <div className="tnum text-[19px] leading-none font-semibold text-ink">{v}</div>
                <div className="mt-1 text-[12px] text-dim">{label}</div>
              </div>
            ))}
          </div>
        </div>
        <p className="mt-3 border-t border-line pt-2.5 text-[12px] text-dim">
          Confidence {impact.confidence}%: how much of this graph an exact parser read, less the imports that could not be resolved.
        </p>
      </Panel>

      {impact.warnings.length > 0 && (
        <Panel eyebrow="Read these before changing it" title="Warnings" className="border-warn/30" flush>
          <div className="divide-y divide-line">
            {impact.warnings.map((w) => (
              <div key={w} className="flex items-start gap-2 px-5 py-2.5">
                <TriangleAlert className="mt-px size-3.5 shrink-0 text-warn" />
                <span className="text-[13px] text-ink-2">{w}</span>
              </div>
            ))}
          </div>
        </Panel>
      )}

      {impact.blastRadius.length === 0 ? (
        <Panel><Empty title="Nothing else depends on it" hint="No other file in the index imports it, uses it or touches its data." /></Panel>
      ) : (
        <Panel eyebrow="Everything that moves with it" title="Blast radius" flush>
          <div className="divide-y divide-line">
            {impact.blastRadius.map((g) => (
              <div key={g.label} className="px-5 py-3">
                <div className="mb-1.5 text-[12px] font-medium text-dim">{g.label} · {g.items.length}</div>
                <div className="flex flex-wrap gap-1">
                  {g.items.map((item) => (onOpen && g.label !== 'Data it touches' ? (
                    <button key={item} onClick={() => onOpen(item)} className="max-w-full truncate rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-[12px] text-ink-2 transition-colors hover:bg-brand/10 hover:text-brand">
                      {item}
                    </button>
                  ) : <Mono key={item}>{item}</Mono>))}
                </div>
              </div>
            ))}
          </div>
        </Panel>
      )}

      <Panel eyebrow="What the OS would do" title="Recommendation" className="accent-left">
        <p className="text-[13.5px] leading-relaxed text-ink-2">{impact.recommendation}</p>
        {actions && <div className="mt-3 flex flex-wrap gap-2">{actions}</div>}
      </Panel>
    </div>
  );
}
