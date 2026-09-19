import { useMemo, useState, type CSSProperties, type ReactNode } from 'react';
import { Plus, Trash2, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Panel, Segmented } from '@/components/os';
import {
  INFRA_KINDS, SERVICE_KINDS, type Adr, type Architecture, type Catalogue, type DataStore, type Environment, type Layer,
  type ScaffoldRepo, type Service,
} from '@/lib/live/blueprints';
import { cn } from '@/lib/utils';
import { TechChips, TechPicker } from './TechPicker';

/* The architecture editor: the layer board (each layer a card — the choice, its alternatives, why), then
   services and data stores, delivery (environments, CI/CD, infrastructure, observability), security and
   conventions, decisions, and the scaffold. Every edit changes the draft the page holds; nothing is sent
   until the person saves, so the diagram beside it follows every keystroke. */

export type Section = 'layers' | 'services' | 'delivery' | 'security' | 'decisions' | 'scaffold';
const SECTIONS: { id: Section; label: string }[] = [
  { id: 'layers', label: 'Layers' }, { id: 'services', label: 'Services and data' }, { id: 'delivery', label: 'Delivery' },
  { id: 'security', label: 'Security and conventions' }, { id: 'decisions', label: 'Decisions' }, { id: 'scaffold', label: 'Scaffold' },
];

/** A name that is also an identifier: lower case, digits and dashes — the form the schema asks for. */
const slug = (s: string, max = 40) => s.toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+/, '').slice(0, max);
const label = (s: string) => s.toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^[^a-z0-9]+/, '').slice(0, 60);

const input = 'focus-brand h-9 w-full min-w-0 rounded-lg border border-line bg-surface-2/60 px-3 text-[13.5px] text-ink placeholder:text-dim focus-visible:outline-none disabled:opacity-60';
const area = 'focus-brand w-full min-w-0 resize-y rounded-lg border border-line bg-surface-2/60 px-3 py-2 text-[13.5px] text-ink placeholder:text-dim focus-visible:outline-none disabled:opacity-60';

function Labelled({ text, children, className }: { text: string; children: ReactNode; className?: string }) {
  return (
    <label className={cn('block min-w-0', className)}>
      <span className="mb-1.5 block text-[12.5px] font-medium text-soft">{text}</span>
      {children}
    </label>
  );
}

/** A list of sentences edited as lines. The text is kept as typed; the list holds its non-empty lines. */
function Lines({ value, onChange, placeholder, rows = 3, disabled, mono }: {
  value: string[]; onChange: (v: string[]) => void; placeholder?: string; rows?: number; disabled?: boolean; mono?: boolean;
}) {
  const [text, setText] = useState(value.join('\n'));
  return (
    <textarea value={text} rows={rows} placeholder={placeholder} disabled={disabled}
      onChange={(e) => { setText(e.target.value); onChange(e.target.value.split('\n').map((l) => l.trim()).filter(Boolean)); }}
      className={cn(area, mono && 'font-mono text-[12.5px]')} />
  );
}

function Remove({ onClick, what, disabled }: { onClick: () => void; what: string; disabled?: boolean }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} aria-label={`Remove ${what}`} title={`Remove ${what}`}
      className="grid size-8 shrink-0 place-items-center rounded-lg text-dim transition-colors hover:bg-danger/10 hover:text-danger disabled:opacity-40">
      <Trash2 className="size-3.5" />
    </button>
  );
}

/** The page keys this by the revision it was loaded from, so its line fields start from what was saved;
    the section shown is the page's, so a save does not send the person back to the first one. */
export function ArchitectureEditor({ draft, change, catalogue, readOnly, section, onSection }: {
  draft: Architecture;
  change: (next: (a: Architecture) => Architecture) => void;
  catalogue: Catalogue;
  readOnly: boolean;
  section: Section;
  onSection: (s: Section) => void;
}) {
  const tech = catalogue.tech;
  const layerInfo = useMemo(() => new Map(catalogue.layers.map((l) => [l.id, l])), [catalogue.layers]);
  const order = catalogue.layers.map((l) => l.id);
  const layers = Object.entries(draft.layers).sort(([a], [b]) => order.indexOf(a) - order.indexOf(b));
  const missing = catalogue.layers.filter((l) => !draft.layers[l.id]);

  const setLayer = (id: string, fn: (l: Layer) => Layer | null) => change((a) => {
    const next = { ...a.layers };
    const made = fn(next[id] ?? { choice: null, alternatives: [], why: '' });
    if (made === null) delete next[id]; else next[id] = made;
    return { ...a, layers: next };
  });
  const setService = (n: number, fn: (s: Service) => Service) =>
    change((a) => ({ ...a, services: a.services.map((s, i) => (i === n ? fn(s) : s)) }));
  const renameNode = (old: string, name: string, kind: 'service' | 'store', n: number) => change((a) => {
    const retarget = (t: string[]) => t.map((x) => (x === old ? name : x));
    return {
      ...a,
      services: a.services.map((s, i) => ({ ...s, name: kind === 'service' && i === n ? name : s.name, talksTo: retarget(s.talksTo) })),
      dataStores: a.dataStores.map((d, i) => (kind === 'store' && i === n ? { ...d, name } : d)),
    };
  });
  const dropNode = (name: string, kind: 'service' | 'store', n: number) => change((a) => ({
    ...a,
    services: (kind === 'service' ? a.services.filter((_, i) => i !== n) : a.services).map((s) => ({ ...s, talksTo: s.talksTo.filter((t) => t !== name) })),
    dataStores: kind === 'store' ? a.dataStores.filter((_, i) => i !== n) : a.dataStores,
  }));
  const nodes = [...draft.services.map((s) => s.name), ...draft.dataStores.map((d) => d.name)];
  const fresh = (base: string) => { let n = 1; let name = base; while (nodes.includes(name)) name = `${base}-${++n}`; return name; };

  return (
    <div className="min-w-0 space-y-4">
      <Segmented options={SECTIONS} value={section} onChange={onSection} />

      {section === 'layers' && (
        <>
          <Panel title="What it is">
            <textarea value={draft.summary} disabled={readOnly} rows={3} placeholder="Two or three sentences on the system and its shape."
              onChange={(e) => change((a) => ({ ...a, summary: e.target.value }))} className={area} aria-label="Summary" />
          </Panel>
          {layers.length === 0 && (
            <p className="rounded-xl border border-dashed border-line px-5 py-6 text-center text-[13px] text-dim">
              No layer is chosen yet. Add the layers this system has — front end, back end, database — below.
            </p>
          )}
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            {layers.map(([id, layer]) => {
              const info = layerInfo.get(id);
              return (
                <section key={id} className="panel min-w-0 rounded-xl border border-line/70 bg-surface px-5 py-4">
                  <div className="mb-3 flex items-center justify-between gap-2">
                    <h3 className="text-[14.5px] font-semibold text-ink">{info?.label ?? id}</h3>
                    {!readOnly && <Remove what={`the ${info?.label ?? id} layer`} onClick={() => setLayer(id, () => null)} />}
                  </div>
                  <div className="space-y-3">
                    <div>
                      <span className="mb-1.5 block text-[12.5px] font-medium text-soft">Choice</span>
                      {readOnly
                        ? <p className="text-[13.5px] text-ink">{tech.find((t) => t.id === layer.choice)?.name ?? layer.choice ?? '—'}</p>
                        : <TechPicker value={layer.choice} tech={tech} category={info?.category} allowNone label={`${info?.label ?? id} technology`}
                            onChange={(v) => setLayer(id, (l) => ({ ...l, choice: v, alternatives: l.alternatives.filter((x) => x !== v) }))} />}
                    </div>
                    <div>
                      <span className="mb-1.5 block text-[12.5px] font-medium text-soft">Alternatives considered</span>
                      {readOnly
                        ? <p className="text-[13px] text-soft">{layer.alternatives.map((x) => tech.find((t) => t.id === x)?.name ?? x).join(', ') || '—'}</p>
                        : <TechChips values={layer.alternatives} tech={tech} category={info?.category} label={`Add an alternative for ${info?.label ?? id}`}
                            onChange={(v) => setLayer(id, (l) => ({ ...l, alternatives: v.filter((x) => x !== l.choice) }))} />}
                    </div>
                    <Labelled text="Why">
                      <textarea value={layer.why} rows={2} disabled={readOnly} placeholder="What makes it the right choice for these answers."
                        onChange={(e) => setLayer(id, (l) => ({ ...l, why: e.target.value }))} className={area} />
                    </Labelled>
                  </div>
                </section>
              );
            })}
          </div>
          {!readOnly && missing.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="mr-1 text-[12.5px] text-dim">Add a layer:</span>
              {missing.map((l) => (
                <button key={l.id} type="button" onClick={() => setLayer(l.id, (x) => x)}
                  className="inline-flex items-center gap-1 rounded-full border border-dashed border-line px-2.5 py-1 text-[12.5px] text-soft hover:border-line-strong hover:text-ink">
                  <Plus className="size-3" />{l.label}
                </button>
              ))}
            </div>
          )}
        </>
      )}

      {section === 'services' && (
        <>
          <Panel title="Services" flush actions={!readOnly && (
            <Button size="sm" variant="outline" onClick={() => change((a) => ({ ...a, services: [...a.services,
              { name: fresh('service'), kind: 'api', tech: '', responsibilities: [], talksTo: [] }] }))}>
              <Plus className="size-3.5" />Add a service
            </Button>
          )}>
            {draft.services.length === 0 ? (
              <p className="px-5 py-6 text-[13px] text-dim">No service yet. A service is something that runs: the web app, the API, a worker.</p>
            ) : (
              <div className="divide-y divide-line/60">
                {draft.services.map((s, n) => (
                  <div key={n} className="space-y-3 px-5 py-4">
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_140px_1fr_auto] sm:items-end">
                      <Labelled text="Name">
                        <input value={s.name} disabled={readOnly} className={cn(input, 'font-mono text-[13px]')}
                          onChange={(e) => renameNode(s.name, slug(e.target.value), 'service', n)} />
                      </Labelled>
                      <Labelled text="Kind">
                        <select value={s.kind} disabled={readOnly} onChange={(e) => setService(n, (x) => ({ ...x, kind: e.target.value }))} className={input}>
                          {SERVICE_KINDS.map((k) => <option key={k} value={k} className="bg-surface">{k}</option>)}
                        </select>
                      </Labelled>
                      <div className="min-w-0">
                        <span className="mb-1.5 block text-[12.5px] font-medium text-soft">Built with</span>
                        <TechPicker value={s.tech || null} tech={tech} label={`${s.name} technology`}
                          onChange={(v) => !readOnly && setService(n, (x) => ({ ...x, tech: v ?? '' }))} />
                      </div>
                      {!readOnly && <Remove what={s.name} onClick={() => dropNode(s.name, 'service', n)} />}
                    </div>
                    <Labelled text="Responsibilities, one per line">
                      <Lines value={s.responsibilities} disabled={readOnly} rows={2} onChange={(v) => setService(n, (x) => ({ ...x, responsibilities: v }))} />
                    </Labelled>
                    <div>
                      <span className="mb-1.5 block text-[12.5px] font-medium text-soft">Talks to</span>
                      <div className="flex flex-wrap gap-1.5">
                        {nodes.filter((x) => x !== s.name).map((x) => {
                          const on = s.talksTo.includes(x);
                          return (
                            <button key={x} type="button" disabled={readOnly} aria-pressed={on}
                              onClick={() => setService(n, (v) => ({ ...v, talksTo: on ? v.talksTo.filter((t) => t !== x) : [...v.talksTo, x] }))}
                              className={cn('rounded-full border px-2.5 py-1 font-mono text-[12px] transition-colors',
                                on ? 'border-brand/50 bg-brand/10 text-ink' : 'border-line text-soft hover:text-ink')}>
                              {x}
                            </button>
                          );
                        })}
                        {nodes.length <= 1 && <span className="text-[12.5px] text-dim">Add another service or a data store to connect it.</span>}
                        {s.talksTo.filter((t) => !nodes.includes(t)).map((t) => (
                          <span key={t} className="inline-flex items-center gap-1 rounded-full bg-warn/12 px-2.5 py-1 font-mono text-[12px] text-warn">
                            {t} is gone
                            {!readOnly && <button onClick={() => setService(n, (v) => ({ ...v, talksTo: v.talksTo.filter((x) => x !== t) }))} aria-label={`Remove ${t}`}><X className="size-3" /></button>}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>
          <Panel title="Data stores" flush actions={!readOnly && (
            <Button size="sm" variant="outline" onClick={() => change((a) => ({ ...a, dataStores: [...a.dataStores, { name: fresh('db'), tech: '', purpose: '' }] }))}>
              <Plus className="size-3.5" />Add a data store
            </Button>
          )}>
            {draft.dataStores.length === 0 ? (
              <p className="px-5 py-6 text-[13px] text-dim">No data store yet: databases, caches, queues and buckets the services keep things in.</p>
            ) : (
              <div className="divide-y divide-line/60">
                {draft.dataStores.map((d, n) => (
                  <div key={n} className="grid grid-cols-1 gap-3 px-5 py-4 sm:grid-cols-[160px_1fr_1.4fr_auto] sm:items-end">
                    <Labelled text="Name">
                      <input value={d.name} disabled={readOnly} className={cn(input, 'font-mono text-[13px]')}
                        onChange={(e) => renameNode(d.name, slug(e.target.value), 'store', n)} />
                    </Labelled>
                    <div className="min-w-0">
                      <span className="mb-1.5 block text-[12.5px] font-medium text-soft">Technology</span>
                      <TechPicker value={d.tech || null} tech={tech} category="database" label={`${d.name} technology`}
                        onChange={(v) => !readOnly && change((a) => ({ ...a, dataStores: a.dataStores.map((x, i): DataStore => (i === n ? { ...x, tech: v ?? '' } : x)) }))} />
                    </div>
                    <Labelled text="Purpose">
                      <input value={d.purpose} disabled={readOnly} className={input}
                        onChange={(e) => change((a) => ({ ...a, dataStores: a.dataStores.map((x, i) => (i === n ? { ...x, purpose: e.target.value } : x)) }))} />
                    </Labelled>
                    {!readOnly && <Remove what={d.name} onClick={() => dropNode(d.name, 'store', n)} />}
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </>
      )}

      {section === 'delivery' && (
        <>
          <Rows<Environment> title="Environments" empty="No environment yet — dev, staging and prod are the usual three." readOnly={readOnly}
            items={draft.environments} add={() => ({ name: slug(['dev', 'staging', 'prod'].find((x) => !draft.environments.some((e) => e.name === x)) ?? 'env'), purpose: '', hosting: '' })}
            set={(v) => change((a) => ({ ...a, environments: v }))}
            fields={[{ key: 'name', label: 'Name', width: '140px', mono: true, clean: (v) => slug(v, 30) }, { key: 'purpose', label: 'Purpose' }, { key: 'hosting', label: 'Where it runs' }]} />
          <Panel title="CI/CD">
            <div className="space-y-4">
              <div className="max-w-sm">
                <span className="mb-1.5 block text-[12.5px] font-medium text-soft">Runs on</span>
                <TechPicker value={draft.ci.tool || null} tech={tech} category="ci-cd" allowNone label="CI/CD tool"
                  onChange={(v) => !readOnly && change((a) => ({ ...a, ci: { ...a.ci, tool: v ?? '' } }))} />
              </div>
              <Rows<{ name: string; does: string }> bare title="Stages" empty="No stage yet." readOnly={readOnly} items={draft.ci.stages}
                add={() => ({ name: 'test', does: '' })} set={(v) => change((a) => ({ ...a, ci: { ...a.ci, stages: v } }))}
                fields={[{ key: 'name', label: 'Stage', width: '160px' }, { key: 'does', label: 'What it does' }]} />
            </div>
          </Panel>
          <Panel title="Infrastructure">
            <div className="space-y-4">
              <Labelled text="Kind" className="max-w-xs">
                <select value={draft.infra.kind} disabled={readOnly} onChange={(e) => change((a) => ({ ...a, infra: { ...a.infra, kind: e.target.value } }))} className={input}>
                  {INFRA_KINDS.map((k) => <option key={k} value={k} className="bg-surface">{k}</option>)}
                </select>
              </Labelled>
              <div>
                <span className="mb-1.5 block text-[12.5px] font-medium text-soft">Tools</span>
                <TechChips values={draft.infra.tools} tech={tech} category="infra" label="Add an infrastructure tool"
                  onChange={(v) => !readOnly && change((a) => ({ ...a, infra: { ...a.infra, tools: v } }))} />
              </div>
              <Labelled text="Notes, one per line">
                <Lines value={draft.infra.notes} disabled={readOnly} onChange={(v) => change((a) => ({ ...a, infra: { ...a.infra, notes: v } }))} />
              </Labelled>
            </div>
          </Panel>
          <Panel title="Observability">
            <div className="space-y-4">
              <div>
                <span className="mb-1.5 block text-[12.5px] font-medium text-soft">Tools</span>
                <TechChips values={draft.observability.tools} tech={tech} category="observability" label="Add an observability tool"
                  onChange={(v) => !readOnly && change((a) => ({ ...a, observability: { ...a.observability, tools: v } }))} />
              </div>
              <Labelled text="Notes, one per line">
                <Lines value={draft.observability.notes} disabled={readOnly} onChange={(v) => change((a) => ({ ...a, observability: { ...a.observability, notes: v } }))} />
              </Labelled>
            </div>
          </Panel>
        </>
      )}

      {section === 'security' && (
        <>
          <Panel title="Security">
            <div className="space-y-4">
              <Labelled text="Authentication">
                <textarea value={draft.security.auth} rows={2} disabled={readOnly} className={area}
                  onChange={(e) => change((a) => ({ ...a, security: { ...a.security, auth: e.target.value } }))} />
              </Labelled>
              <Labelled text="Secrets">
                <textarea value={draft.security.secrets} rows={2} disabled={readOnly} className={area}
                  onChange={(e) => change((a) => ({ ...a, security: { ...a.security, secrets: e.target.value } }))} />
              </Labelled>
              <Labelled text="Notes (OWASP and the rest), one per line">
                <Lines value={draft.security.notes} rows={4} disabled={readOnly} onChange={(v) => change((a) => ({ ...a, security: { ...a.security, notes: v } }))} />
              </Labelled>
            </div>
          </Panel>
          <Panel title="Folder layout">
            <textarea value={draft.folderLayout} rows={8} disabled={readOnly} aria-label="Folder layout" placeholder={'web/\n  src/\napi/\n  app/'}
              onChange={(e) => change((a) => ({ ...a, folderLayout: e.target.value }))} className={cn(area, 'font-mono text-[12.5px]')} />
          </Panel>
          <Panel title="Conventions">
            <Lines value={draft.conventions} rows={5} disabled={readOnly} placeholder="Every schema change is a migration"
              onChange={(v) => change((a) => ({ ...a, conventions: v }))} />
          </Panel>
        </>
      )}

      {section === 'decisions' && (
        <Panel title="Decisions" flush actions={!readOnly && (
          <Button size="sm" variant="outline" onClick={() => change((a) => ({ ...a, adrs: [...a.adrs, { title: '', decision: '', why: '', alternatives: [] }] }))}>
            <Plus className="size-3.5" />Add a decision
          </Button>
        )}>
          <p className="px-5 pt-3 text-[12.5px] text-dim">
            Finalizing writes one decision for every layer with a choice, plus each one here, into Memory under Decisions.
          </p>
          {draft.adrs.length === 0 ? (
            <p className="px-5 py-5 text-[13px] text-dim">No decision of its own yet. Write down the choices that are not a single layer — a monolith first, events over calls.</p>
          ) : (
            <div className="divide-y divide-line/60">
              {draft.adrs.map((d, n) => {
                const set = (fn: (x: Adr) => Adr) => change((a) => ({ ...a, adrs: a.adrs.map((x, i) => (i === n ? fn(x) : x)) }));
                return (
                  <div key={n} className="space-y-3 px-5 py-4">
                    <div className="flex items-end gap-3">
                      <Labelled text="Title" className="flex-1">
                        <input value={d.title} disabled={readOnly} className={input} onChange={(e) => set((x) => ({ ...x, title: e.target.value }))} />
                      </Labelled>
                      {!readOnly && <Remove what={d.title || 'this decision'} onClick={() => change((a) => ({ ...a, adrs: a.adrs.filter((_, i) => i !== n) }))} />}
                    </div>
                    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                      <Labelled text="Decision"><textarea value={d.decision} rows={2} disabled={readOnly} className={area} onChange={(e) => set((x) => ({ ...x, decision: e.target.value }))} /></Labelled>
                      <Labelled text="Why"><textarea value={d.why} rows={2} disabled={readOnly} className={area} onChange={(e) => set((x) => ({ ...x, why: e.target.value }))} /></Labelled>
                    </div>
                    <Labelled text="Alternatives considered, one per line">
                      <Lines value={d.alternatives} rows={2} disabled={readOnly} onChange={(v) => set((x) => ({ ...x, alternatives: v }))} />
                    </Labelled>
                  </div>
                );
              })}
            </div>
          )}
        </Panel>
      )}

      {section === 'scaffold' && (
        <ScaffoldRecipe repos={draft.scaffold.repos} readOnly={readOnly} set={(repos) => change((a) => ({ ...a, scaffold: { repos } }))} />
      )}
    </div>
  );
}

interface FieldSpec<T> { key: keyof T & string; label: string; width?: string; mono?: boolean; clean?: (v: string) => string }

/** A small editable table of short text fields, one row per item. */
function Rows<T extends Record<keyof T, string>>({ title, items, set, add, fields, empty, readOnly, bare }: {
  title: string; items: T[]; set: (v: T[]) => void; add: () => T; fields: FieldSpec<T>[]; empty: string; readOnly: boolean; bare?: boolean;
}) {
  const cols = `${fields.map((f) => f.width ?? '1fr').join(' ')} auto`;
  const body = (
    <>
      {items.length === 0 ? <p className={cn('text-[13px] text-dim', !bare && 'px-5 py-5')}>{empty}</p> : (
        <div className={cn(bare ? 'space-y-3' : 'divide-y divide-line/60')}>
          {items.map((item, n) => (
            <div key={n} className={cn('grid grid-cols-1 gap-3 sm:items-end', !bare && 'px-5 py-4', 'sm:[grid-template-columns:var(--cols)]')}
              style={{ '--cols': cols } as CSSProperties}>
              {fields.map((f) => (
                <Labelled key={f.key} text={f.label}>
                  <input value={item[f.key]} disabled={readOnly} className={cn(input, f.mono && 'font-mono text-[13px]')}
                    onChange={(e) => set(items.map((x, i) => (i === n ? { ...x, [f.key]: f.clean ? f.clean(e.target.value) : e.target.value } : x)))} />
                </Labelled>
              ))}
              {!readOnly && <Remove what={item[fields[0].key] || 'this row'} onClick={() => set(items.filter((_, i) => i !== n))} />}
            </div>
          ))}
        </div>
      )}
      {bare && !readOnly && (
        <Button size="sm" variant="outline" className="mt-3" onClick={() => set([...items, add()])}><Plus className="size-3.5" />Add</Button>
      )}
    </>
  );
  if (bare) return <div><span className="mb-2 block text-[12.5px] font-medium text-soft">{title}</span>{body}</div>;
  return (
    <Panel title={title} flush actions={!readOnly && <Button size="sm" variant="outline" onClick={() => set([...items, add()])}><Plus className="size-3.5" />Add</Button>}>
      {body}
    </Panel>
  );
}

/** The scaffold recipe: the repositories to make and, in each, the files agents are asked to write. */
function ScaffoldRecipe({ repos, set, readOnly }: { repos: ScaffoldRepo[]; set: (r: ScaffoldRepo[]) => void; readOnly: boolean }) {
  const setRepo = (n: number, fn: (r: ScaffoldRepo) => ScaffoldRepo) => set(repos.map((r, i) => (i === n ? fn(r) : r)));
  const fresh = () => { let n = 1; let l = 'app'; while (repos.some((r) => r.label === l)) l = `app-${++n}`; return l; };
  return (
    <div className="space-y-4">
      <p className="text-[13px] leading-relaxed text-soft">
        Scaffolding makes one empty repository per entry here and compiles a plan that asks agents to write these files.
        They are written in worktrees and land only after the review and your signature. Each repository also gets an AGENTS.md.
      </p>
      {repos.length === 0 && <p className="rounded-xl border border-dashed border-line px-5 py-6 text-center text-[13px] text-dim">No repository in the recipe yet.</p>}
      {repos.map((r, n) => (
        <Panel key={n} title={<span className="font-mono">{r.label || 'repository'}</span>} flush actions={!readOnly && (
          <Remove what={`the ${r.label} repository`} onClick={() => set(repos.filter((_, i) => i !== n))} />
        )}>
          <div className="grid grid-cols-1 gap-3 px-5 py-4 sm:grid-cols-[180px_1fr]">
            <Labelled text="Label (its folder)">
              <input value={r.label} disabled={readOnly} className={cn(input, 'font-mono text-[13px]')} onChange={(e) => setRepo(n, (x) => ({ ...x, label: label(e.target.value) }))} />
            </Labelled>
            <Labelled text="Layout">
              <input value={r.layout} disabled={readOnly} className={input} onChange={(e) => setRepo(n, (x) => ({ ...x, layout: e.target.value }))} />
            </Labelled>
          </div>
          <div className="divide-y divide-line/60 border-t border-line/60">
            {r.files.map((f, i) => (
              <div key={i} className="grid grid-cols-1 gap-2 px-5 py-3 sm:grid-cols-[minmax(160px,0.8fr)_1.6fr_auto] sm:items-start">
                <input value={f.path} disabled={readOnly} aria-label="File path" placeholder="src/main.ts" className={cn(input, 'font-mono text-[12.5px]')}
                  onChange={(e) => setRepo(n, (x) => ({ ...x, files: x.files.map((y, j) => (j === i ? { ...y, path: e.target.value.replace(/^\/+/, '') } : y)) }))} />
                <textarea value={f.template} disabled={readOnly} rows={1} aria-label={`What ${f.path} holds`} placeholder="What this file should hold"
                  className={area} onChange={(e) => setRepo(n, (x) => ({ ...x, files: x.files.map((y, j) => (j === i ? { ...y, template: e.target.value } : y)) }))} />
                {!readOnly && <Remove what={f.path || 'this file'} onClick={() => setRepo(n, (x) => ({ ...x, files: x.files.filter((_, j) => j !== i) }))} />}
              </div>
            ))}
          </div>
          {!readOnly && (
            <div className="px-5 py-3">
              <Button size="sm" variant="ghost" onClick={() => setRepo(n, (x) => ({ ...x, files: [...x.files, { path: '', template: '' }] }))}><Plus className="size-3.5" />Add a file</Button>
            </div>
          )}
        </Panel>
      ))}
      {!readOnly && (
        <Button size="sm" variant="outline" onClick={() => set([...repos, { label: fresh(), layout: '', files: [{ path: 'README.md', template: 'What the repository is and how to run it.' }] }])}>
          <Plus className="size-3.5" />Add a repository
        </Button>
      )}
    </div>
  );
}
