import { useMemo, useState } from 'react';
import { Download, Loader2, Plus, Search, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Empty, Field, KV, Panel, Segmented, Tag } from '@/components/os';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { blueprintsApi, download, techName, type Catalogue, type TemplateSummary } from '@/lib/live/blueprints';
import { useRemote } from '@/lib/remote';
import { Diagram } from './Diagram';

/* The template bank: the architectures that ship with NeuroCode (server/app/data/blueprints/*.json) and the
   ones people here saved or imported. Browse, preview one whole — its layers, services, diagram and the
   files its scaffold asks for — start a blueprint from it, export it, or delete one of your own. */

const failed = (e: unknown) => (e instanceof ApiError ? e.message : 'The API did not answer.');

export function TemplateBank({ catalogue, onUse, onChanged }: {
  catalogue: Catalogue;
  /** Start a new blueprint from this template. */
  onUse: (templateId: string) => void;
  /** The bank changed (a template deleted): read the catalogue again. */
  onChanged: () => void;
}) {
  const { can, user } = useAuth();
  const [q, setQ] = useState('');
  const [which, setWhich] = useState<'all' | 'catalogue' | 'mine'>('all');
  const [open, setOpen] = useState<string | null>(null);
  const byId = useMemo(() => new Map(catalogue.tech.map((t) => [t.id, t])), [catalogue.tech]);
  const all = useMemo(() => [...catalogue.mine, ...catalogue.templates], [catalogue]);
  const shown = all.filter((t) => (which === 'all' || t.source === which)
    && (!q.trim() || `${t.name} ${t.summary} ${Object.values(t.layers).join(' ')}`.toLowerCase().includes(q.trim().toLowerCase())));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Field value={q} onChange={setQ} placeholder="Search templates and technologies" icon={<Search className="size-3.5" />} className="w-full sm:w-80" onClear={() => setQ('')} />
        <Segmented options={[{ id: 'all', label: `All · ${all.length}` }, { id: 'catalogue', label: 'Catalogue' }, { id: 'mine', label: `Yours · ${catalogue.mine.length}` }]}
          value={which} onChange={setWhich} />
      </div>
      {shown.length === 0 ? (
        <Panel>
          <Empty title={which === 'mine' ? 'No template of your own yet' : 'No template matches'}
            hint={which === 'mine' ? 'Save a blueprint as a template, or import a JSON or YAML file.' : 'Try other words.'} />
        </Panel>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 2xl:grid-cols-3">
          {shown.map((t) => <Card key={t.id} t={t} byId={byId} onOpen={() => setOpen(t.id)} />)}
        </div>
      )}
      <Preview id={open} onClose={() => setOpen(null)} byId={byId} canDesign={can('plans:compile')}
        onUse={(id) => { setOpen(null); onUse(id); }}
        canDelete={(t) => t.source === 'mine' && (t.createdById === user?.id || can('workspace:admin'))}
        onDeleted={() => { setOpen(null); onChanged(); }} />
    </div>
  );
}

function Card({ t, byId, onOpen }: { t: TemplateSummary; byId: Map<string, { name: string }>; onOpen: () => void }) {
  const layers = [...new Set(Object.values(t.layers).filter(Boolean) as string[])];
  return (
    <button onClick={onOpen} className="panel flex min-w-0 flex-col rounded-xl border border-line/70 bg-surface px-5 py-4 text-left transition-colors hover:bg-surface-2/50">
      <span className="flex items-start justify-between gap-3">
        <span className="text-[14.5px] font-semibold text-ink">{t.name}</span>
        {t.source === 'mine' && <Tag tone="violet">yours</Tag>}
      </span>
      <span className="mt-1 line-clamp-3 text-[13px] leading-relaxed text-soft">{t.summary || t.description}</span>
      <span className="mt-3 flex flex-wrap gap-1.5">
        {layers.slice(0, 6).map((id) => <span key={id} className="rounded-full bg-surface-2 px-2 py-0.5 text-[11.5px] text-ink-2">{byId.get(id)?.name ?? id}</span>)}
        {layers.length > 6 && <span className="px-1 text-[11.5px] text-dim">+{layers.length - 6}</span>}
      </span>
      <span className="mt-3 text-[12px] text-dim">{t.services} service{t.services === 1 ? '' : 's'} · {t.repos} repositor{t.repos === 1 ? 'y' : 'ies'} in its scaffold</span>
    </button>
  );
}

function Preview({ id, onClose, byId, canDesign, onUse, canDelete, onDeleted }: {
  id: string | null; onClose: () => void; byId: Map<string, { name: string }>; canDesign: boolean;
  onUse: (id: string) => void; canDelete: (t: { source: string; createdById?: string | null }) => boolean; onDeleted: () => void;
}) {
  const got = useRemote(id ? `template:${id}` : null, () => blueprintsApi.template(id ?? ''));
  const [deleting, setDeleting] = useState(false);
  const t = got.data;
  const name = (x: string | null) => techName(byId, x);

  const exportAs = async (format: 'json' | 'yaml') => {
    if (!t) return;
    try { download(await blueprintsApi.exportTemplate(t.id, format)); } catch (e) { toast.error('Not exported', { description: failed(e) }); }
  };
  const remove = async () => {
    if (!t) return;
    setDeleting(true);
    try {
      await blueprintsApi.removeTemplate(t.id);
      toast.success(`${t.name} deleted`);
      onDeleted();
    } catch (e) {
      toast.error('Not deleted', { description: failed(e) });
    } finally {
      setDeleting(false);
    }
  };

  return (
    <Sheet open={!!id} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-full gap-0 overflow-y-auto p-0 sm:max-w-2xl">
        <SheetHeader className="border-b border-line px-5 py-4">
          <SheetTitle className="pr-8 text-[16px]">{t?.name ?? 'Template'}</SheetTitle>
          <SheetDescription className="text-[13px]">{t ? (t.source === 'mine' ? `Saved by ${t.createdBy ?? 'someone who has left'}` : 'Ships with NeuroCode') : ' '}</SheetDescription>
        </SheetHeader>
        {got.loading && <p className="flex items-center gap-2 px-5 py-8 text-[13px] text-dim"><Loader2 className="size-4 animate-spin" />Reading the template…</p>}
        {got.error && <p className="px-5 py-8 text-[13px] text-danger">{got.error}</p>}
        {t && (
          <div className="space-y-5 px-5 py-5">
            <p className="text-[14px] leading-relaxed text-ink-2">{t.summary}</p>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" disabled={!canDesign} onClick={() => onUse(t.id)} title={canDesign ? undefined : 'Needs the plans:compile permission'}>
                <Plus className="size-3.5" />Use template
              </Button>
              <Button size="sm" variant="outline" onClick={() => void exportAs('json')}><Download className="size-3.5" />JSON</Button>
              <Button size="sm" variant="outline" onClick={() => void exportAs('yaml')}><Download className="size-3.5" />YAML</Button>
              {canDelete(t) && (
                <Button size="sm" variant="ghost" className="text-danger" disabled={deleting} onClick={() => void remove()}>
                  <Trash2 className="size-3.5" />Delete
                </Button>
              )}
            </div>
            {(t.fitsWhen.length > 0 || t.avoidWhen.length > 0) && (
              <section>
                <h3 className="mb-2 text-[13px] font-semibold text-ink">When it fits</h3>
                <div className="flex flex-wrap gap-1.5">
                  {t.fitsWhen.map((c) => <Tag key={`f${c.says}`} tone="ok" className="whitespace-normal">{c.says}</Tag>)}
                  {t.avoidWhen.map((c) => <Tag key={`a${c.says}`} tone="warn" className="whitespace-normal">avoid when {c.says}</Tag>)}
                </div>
              </section>
            )}
            <section>
              <h3 className="mb-1 text-[13px] font-semibold text-ink">Layers</h3>
              {Object.entries(t.spec.layers).map(([k, l]) => (
                <KV key={k} k={k} wrap v={<>{name(l.choice)}{l.alternatives.length > 0 && <span className="text-dim"> · or {l.alternatives.map(name).join(', ')}</span>}</>} />
              ))}
            </section>
            <section>
              <h3 className="mb-2 text-[13px] font-semibold text-ink">Diagram</h3>
              <Diagram text={t.diagram} />
            </section>
            {t.spec.scaffold.repos.map((r) => (
              <section key={r.label}>
                <h3 className="mb-1 text-[13px] font-semibold text-ink">Scaffold · <span className="font-mono">{r.label}</span></h3>
                <p className="mb-2 text-[12.5px] text-dim">{r.layout}</p>
                <ul className="space-y-1">
                  {r.files.map((f) => <li key={f.path} className="text-[12.5px] text-soft"><code className="font-mono text-ink-2">{f.path}</code> — {f.template}</li>)}
                </ul>
              </section>
            ))}
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
