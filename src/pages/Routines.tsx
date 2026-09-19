import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  CalendarClock, Check, Copy, FolderGit2, Loader2, Pause, Pencil, Play, Plus, RefreshCw, Trash2, Webhook,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, ListRow, Field, Segmented, SelectField, KV, Empty, SectionTitle, type Tone } from '@/components/os';
import { API_BASE, ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useData } from '@/lib/data';
import { useProject } from '@/lib/project-context';
import { useRemote } from '@/lib/remote';
import { workflowsApi } from '@/lib/live/workflows';
import {
  DAY_NAMES, presetCron, readPreset, routinesApi,
  type CadencePreview, type FireOutcome, type Paged, type Preset, type PresetId, type Routine, type RoutineFire, type RoutineInput, type WebhookToken,
} from '@/lib/live/routines';
import { ago } from '@/lib/time';
import type { Project } from '@/types';

/* Routines: a workflow or a requirement on a cadence, a webhook, or "Run now". A fire does what a person
   on the Workflows screen does — compile or write a plan and dispatch it — so its runs stop at the same
   gates and the same signature. Times are UTC, and say so. */

const OUTCOME_TONE: Record<FireOutcome, Tone> = { firing: 'brand', fired: 'ok', refused: 'warn', failed: 'danger', skipped: 'neutral' };
const TRIGGER_WORD = { schedule: 'On schedule', manual: 'Run now', webhook: 'Webhook' } as const;
const reason = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

/** "Fri 18 Sep, 09:00 UTC" — the moment itself, in the zone every cadence is written in. */
function utc(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  const weekday = d.toLocaleString('en', { weekday: 'short', timeZone: 'UTC' });
  const month = d.toLocaleString('en', { month: 'short', timeZone: 'UTC' });
  return `${weekday} ${d.getUTCDate()} ${month}, ${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')} UTC`;
}

/** "in 3 h" · "in 12 min" — how long until a moment in the future. */
function until(iso: string | null): string {
  if (!iso) return '';
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 60_000) return 'within a minute';
  if (ms < 3_600_000) return `in ${Math.round(ms / 60_000)} min`;
  if (ms < 86_400_000) return `in ${Math.round(ms / 3_600_000)} h`;
  return `in ${Math.round(ms / 86_400_000)} d`;
}

export default function Routines() {
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const { can } = useAuth();
  const { activity, runs } = useData();
  const { all } = useProject();
  const withCode = useMemo(() => all.filter((p) => p.source), [all]);
  const mayWrite = can('workflows:write');
  const mayFire = can('workflows:write', 'plans:compile', 'plans:decide');

  const list = useRemote('routines:list', () => routinesApi.list(null));
  // A fire, a skipped fire or a run changing anywhere reaches the store; the routines are read again when one does.
  const stamp = useMemo(() => {
    const fired = activity.find((e) => e.action.startsWith('Routine'))?.id ?? '';
    return `${fired}|${runs.slice(0, 30).map((r) => `${r.ref}:${r.status}`).join(',')}`;
  }, [activity, runs]);
  const reload = useRef(() => {});
  useLayoutEffect(() => { reload.current = list.reload; });
  const seen = useRef(stamp);
  useEffect(() => {
    if (seen.current === stamp) return;
    seen.current = stamp;
    reload.current();
  }, [stamp]);

  const items = list.data?.items ?? [];
  const wanted = params.get('id');
  const selected = items.find((r) => r.id === wanted) ?? items[0] ?? null;
  const [editing, setEditing] = useState<Routine | 'new' | null>(null);
  const [shown, setShown] = useState<WebhookToken | null>(null);
  const [removing, setRemoving] = useState<Routine | null>(null);

  const select = (id: string) => setParams((p) => { p.set('id', id); return p; }, { replace: true });

  return (
    <Page>
      <PageHeader
        title="Routines"
        subtitle="Put a requirement or a workflow on a cadence, behind a webhook, or on Run now. Each fire compiles and dispatches a plan as you would, so the project’s first test run still waits for your approval and every run stops at your signature. Nothing merges unattended. Times are UTC."
        actions={mayWrite && (
          <Button size="sm" onClick={() => setEditing('new')} disabled={!withCode.length}
            title={withCode.length ? undefined : 'No project has code on this machine yet'}>
            <Plus className="size-3.5" />New routine
          </Button>
        )}
      />
      <PageBody className="space-y-4">
        {list.error ? (
          <Empty title="Routines did not load" hint={list.error} action={<Button size="sm" variant="outline" onClick={list.reload}>Try again</Button>} />
        ) : !list.data ? (
          <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Loading routines…" />
        ) : items.length === 0 ? (
          <Panel>
            <Empty icon={<CalendarClock className="size-6" />} title="No routines yet"
              hint={withCode.length
                ? 'A routine compiles a requirement, or runs a workflow, on a cadence you choose — hourly, daily, on weekdays, weekly or any cron — and from a webhook when you add one.'
                : 'A routine’s runs branch from a real repository. Onboard a project with its code first.'}
              action={withCode.length
                ? mayWrite && <Button size="sm" variant="outline" onClick={() => setEditing('new')}><Plus className="size-3.5" />New routine</Button>
                : <Button size="sm" variant="outline" onClick={() => nav('/projects')}><FolderGit2 className="size-3.5" />Open Projects</Button>} />
          </Panel>
        ) : (
          <div className="flex min-h-0 flex-col gap-4 md:flex-row md:items-start">
            <div className="w-full shrink-0 overflow-hidden rounded-xl border border-line/70 bg-surface md:w-[340px]">
              <div className="divide-y divide-line/50">
                {items.map((r) => (
                  <ListRow key={r.id} active={r.id === selected?.id} onClick={() => select(r.id)}>
                    <div className="flex items-center gap-2">
                      <Dot state={!r.enabled ? 'todo' : r.waiting ? 'waiting' : 'ok'} pulse={r.last?.outcome === 'firing'} />
                      <span className="min-w-0 flex-1 truncate text-[14px] font-medium text-ink">{r.name}</span>
                      {!r.enabled && <Tag>Paused</Tag>}
                    </div>
                    <div className="mt-1 truncate pl-4 text-[12.5px] text-dim">{r.projectName ?? r.projectId} · {r.cadenceLabel}</div>
                    <div className="mt-0.5 flex items-center gap-2 pl-4 text-[12px] text-dim">
                      <span className="truncate">{r.enabled && r.nextAt ? `Next ${until(r.nextAt)}` : r.enabled ? 'On demand' : 'Not scheduled'}</span>
                      {r.last && <Tag tone={OUTCOME_TONE[r.last.outcome]} className="ml-auto">{r.last.outcome}</Tag>}
                    </div>
                  </ListRow>
                ))}
              </div>
            </div>
            {selected && (
              <Detail key={selected.id} routine={selected} mayWrite={mayWrite} mayFire={mayFire}
                onChanged={list.reload} onEdit={() => setEditing(selected)} onRemove={() => setRemoving(selected)} onToken={setShown} />
            )}
          </div>
        )}
      </PageBody>

      {editing && (
        <Editor initial={editing === 'new' ? null : editing} projects={withCode}
          onClose={() => setEditing(null)}
          onSaved={(saved) => { setEditing(null); list.reload(); select(saved.id); }} />
      )}
      {shown && <TokenDialog token={shown} onClose={() => setShown(null)} />}
      {removing && (
        <Dialog open onOpenChange={(o) => { if (!o) setRemoving(null); }}>
          <DialogContent className="sm:max-w-[440px]">
            <DialogHeader>
              <DialogTitle>Delete {removing.name}?</DialogTitle>
              <DialogDescription>It stops firing, and its history and webhook go with it. The plans and runs it started stay where they are.</DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setRemoving(null)}>Cancel</Button>
              <Button variant="destructive" onClick={() => {
                const target = removing;
                setRemoving(null);
                routinesApi.remove(target.id).then(
                  () => { toast.success(`${target.name} deleted`); setParams({}, { replace: true }); list.reload(); },
                  (e: unknown) => toast.error('Not deleted', { description: reason(e, 'The local API did not answer.') }),
                );
              }}><Trash2 className="size-3.5" />Delete</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </Page>
  );
}

function Detail({ routine: r, mayWrite, mayFire, onChanged, onEdit, onRemove, onToken }: {
  routine: Routine; mayWrite: boolean; mayFire: boolean;
  onChanged: () => void; onEdit: () => void; onRemove: () => void; onToken: (t: WebhookToken) => void;
}) {
  const nav = useNavigate();
  const [busy, setBusy] = useState<'run' | 'pause' | 'token' | null>(null);
  // Keyed on the last fire, so a new one (or its outcome) is read the moment the list sees it.
  const fires = useRemote(`routines:fires:${r.id}:${r.last?.id ?? ''}:${r.last?.outcome ?? ''}:${r.last?.runStatus ?? ''}`, () => routinesApi.fires(r.id));
  // Older pages belong to the first page they were read after; a fresh first page starts the list again.
  const [extra, setExtra] = useState<{ after: Paged<RoutineFire> | null; items: RoutineFire[]; next: number | null }>({ after: null, items: [], next: null });
  const current = extra.after !== null && extra.after === fires.data;
  const older = current ? extra.items : [];
  const more = current ? extra.next : fires.data?.nextOffset ?? null;

  const act = async (what: 'run' | 'pause', call: () => Promise<unknown>, ok: string, description?: string) => {
    setBusy(what);
    try {
      await call();
      toast.success(ok, description ? { description } : undefined);
      onChanged();
    } catch (e) {
      toast.error(what === 'run' ? 'Did not fire' : 'Not changed', { description: reason(e, 'The local API did not answer.') });
    } finally {
      setBusy(null);
    }
  };

  const issue = async () => {
    setBusy('token');
    try {
      onToken(await routinesApi.issueToken(r.id));
      onChanged();
    } catch (e) {
      toast.error('No webhook made', { description: reason(e, 'The local API did not answer.') });
    } finally {
      setBusy(null);
    }
  };

  const revoke = async () => {
    try {
      await routinesApi.revokeToken(r.id);
      toast.success('Webhook removed', { description: 'Its token no longer fires this routine.' });
      onChanged();
    } catch (e) {
      toast.error('Not removed', { description: reason(e, 'The local API did not answer.') });
    }
  };

  const loadMore = async () => {
    if (more === null || !fires.data) return;
    const first = fires.data;
    try {
      const page = await routinesApi.fires(r.id, more);
      setExtra({ after: first, items: [...older, ...page.items], next: page.nextOffset });
    } catch (e) {
      toast.error('Older fires did not load', { description: reason(e, 'The local API did not answer.') });
    }
  };

  const history = [...(fires.data?.items ?? []), ...older];

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <Panel
        title={<span className="flex items-center gap-2">{r.name}{r.enabled ? <Tag tone="ok">Active</Tag> : <Tag>Paused</Tag>}</span>}
        eyebrow={r.projectName ?? r.projectId}
        actions={(
          <div className="flex flex-wrap items-center gap-1.5">
            {mayFire && (
              <Button size="sm" onClick={() => void act('run', () => routinesApi.runNow(r.id), `${r.name} is firing`, 'It compiles and dispatches now; its runs stop at your signature.')}
                disabled={busy !== null || !!r.waiting} title={r.waiting ?? 'Fire it now, as you'}>
                {busy === 'run' ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}Run now
              </Button>
            )}
            {mayWrite && (
              <>
                <Button size="sm" variant="outline" disabled={busy !== null}
                  onClick={() => void act('pause', () => routinesApi.update(r.id, { enabled: !r.enabled }), r.enabled ? `${r.name} paused` : `${r.name} resumed`)}>
                  {r.enabled ? <><Pause className="size-3.5" />Pause</> : <><Play className="size-3.5" />Resume</>}
                </Button>
                <Button size="icon-sm" variant="ghost" aria-label="Edit" onClick={onEdit}><Pencil className="size-3.5" /></Button>
                <Button size="icon-sm" variant="ghost" aria-label="Delete" onClick={onRemove}><Trash2 className="size-3.5" /></Button>
              </>
            )}
          </div>
        )}
      >
        {r.waiting && (
          <p className="mb-3 rounded-lg bg-warn/10 px-3 py-2 text-[13px] leading-relaxed text-ink-2">
            <span className="font-medium text-warn">Holding. </span>{r.waiting}
          </p>
        )}
        <KV k="Runs" v={r.what === 'workflow' ? `Workflow ${r.workflowName ?? r.workflowId}` : 'A requirement, compiled each time'} />
        <div className="border-b border-line/50 py-2">
          <div className="text-[13px] text-dim">{r.what === 'workflow' ? 'Input' : 'Requirement'}</div>
          <p className="mt-1 text-[13.5px] leading-relaxed whitespace-pre-line text-ink-2 [overflow-wrap:anywhere]">{r.requirement}</p>
        </div>
        <KV k="Cadence" v={<span className="flex items-center justify-end gap-2">{r.cadenceLabel}{r.cadence && <Mono>{r.cadence}</Mono>}</span>} />
        <KV k="Next fire" v={!r.enabled ? 'Paused' : r.nextAt ? `${utc(r.nextAt)} · ${until(r.nextAt)}` : 'Only on Run now or its webhook'} />
        <KV k="Last fired" v={r.lastFiredAt ? `${utc(r.lastFiredAt)} · ${ago(r.lastFiredAt)}` : 'Never'} />
        <KV k="Acts as" v={r.createdBy ? `${r.createdBy} on schedule and webhook · you on Run now` : 'Its maker is gone — edit and save it to take it over'} wrap />
      </Panel>

      <Panel title={<span className="flex items-center gap-2"><Webhook className="size-4 text-dim" />Webhook</span>}
        actions={mayWrite && (r.webhook ? (
          <div className="flex items-center gap-1.5">
            <Button size="sm" variant="outline" onClick={() => void issue()} disabled={busy !== null}><RefreshCw className="size-3.5" />Replace token</Button>
            <Button size="sm" variant="ghost" onClick={() => void revoke()}>Remove</Button>
          </div>
        ) : (
          <Button size="sm" variant="outline" onClick={() => void issue()} disabled={busy !== null}>
            {busy === 'token' ? <Loader2 className="size-3.5 animate-spin" /> : <Plus className="size-3.5" />}Add webhook
          </Button>
        ))}>
        <p className="text-[13px] leading-relaxed text-soft">
          {r.webhook
            ? 'A POST with this routine’s token fires it. Whatever the request carries is kept as a quoted excerpt and handed to the compiler as data — never as instructions. The token was shown once; replace it if it is lost.'
            : 'No webhook. Add one to fire this routine from CI, an alert or any service that can send a POST. The token is shown once and only its hash is kept.'}
        </p>
      </Panel>

      <Panel title={<span className="flex items-center gap-2">Fires{fires.data && <span className="tnum text-dim">{fires.data.total}</span>}</span>} flush>
        {fires.error ? (
          <Empty title="Fires did not load" hint={fires.error} action={<Button size="sm" variant="outline" onClick={fires.reload}>Try again</Button>} />
        ) : !fires.data ? (
          <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Loading fires…" />
        ) : history.length === 0 ? (
          <Empty title="It has not fired yet" hint={r.enabled && r.nextAt ? `The first fire is ${utc(r.nextAt)}.` : 'Run it now, or add a cadence or a webhook.'} />
        ) : (
          <div className="divide-y divide-line/50">
            {history.map((f) => (
              <div key={f.id} className="px-5 py-3">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <Tag tone={OUTCOME_TONE[f.outcome]}>{f.outcome}</Tag>
                  <span className="text-[13px] text-ink-2">{TRIGGER_WORD[f.trigger]}</span>
                  <span className="text-[12.5px] text-dim">{utc(f.at)}</span>
                  <span className="ml-auto flex items-center gap-1.5">
                    {f.planRef && <button onClick={() => nav(`/plans?ref=${encodeURIComponent(f.planRef!)}`)}><Mono tone="brand">{f.planRef}</Mono></button>}
                    {f.runRef && <button onClick={() => nav(`/runs?ref=${encodeURIComponent(f.runRef!)}`)}><Mono tone="brand">{f.runRef}</Mono></button>}
                    {f.runStatus && <span className="text-[12px] text-dim">{f.runStatus}</span>}
                  </span>
                </div>
                {f.detail && <p className="mt-1.5 text-[13px] leading-relaxed text-soft [overflow-wrap:anywhere]">{f.detail}</p>}
                {f.payloadExcerpt && (
                  <details className="mt-1.5">
                    <summary className="cursor-pointer text-[12.5px] text-dim hover:text-ink-2">What the webhook sent (quoted, as data)</summary>
                    <pre className="mt-1.5 max-h-48 overflow-auto rounded-lg bg-base p-3 font-mono text-[12px] whitespace-pre-wrap text-ink-2 ring-1 ring-line/60 ring-inset [overflow-wrap:anywhere]">{f.payloadExcerpt}</pre>
                  </details>
                )}
              </div>
            ))}
            {more !== null && (
              <button onClick={() => void loadMore()} className="w-full px-5 py-3 text-left text-[13px] text-soft transition-colors hover:bg-surface-2/60 hover:text-ink">
                Older fires →
              </button>
            )}
          </div>
        )}
      </Panel>
    </div>
  );
}

function TokenDialog({ token, onClose }: { token: WebhookToken; onClose: () => void }) {
  const [copied, setCopied] = useState<string | null>(null);
  const url = new URL(`${API_BASE}${token.path}`, window.location.origin).toString();
  const curl = `curl -X POST '${url}' \\\n  -H 'Authorization: Bearer ${token.token}' \\\n  -H 'Content-Type: application/json' \\\n  -d '{"event": "what happened"}'`;
  const copy = (what: string, text: string) => {
    navigator.clipboard.writeText(text).then(
      () => { setCopied(what); window.setTimeout(() => setCopied(null), 1500); },
      () => toast.error('Could not copy', { description: 'Select the text and copy it yourself.' }),
    );
  };
  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="sm:max-w-[600px]">
        <DialogHeader>
          <DialogTitle>Webhook token</DialogTitle>
          <DialogDescription>Copy it now: it is shown this once, and only its hash is kept. Any earlier token for this routine has stopped working.</DialogDescription>
        </DialogHeader>
        <div className="grid min-w-0 gap-3">
          <div>
            <SectionTitle right={<Button size="xs" variant="ghost" onClick={() => copy('token', token.token)}>{copied === 'token' ? <Check className="size-3" /> : <Copy className="size-3" />}Copy</Button>}>Token</SectionTitle>
            <pre className="overflow-x-auto rounded-lg bg-base p-3 font-mono text-[12.5px] text-ink ring-1 ring-line/60 ring-inset [overflow-wrap:anywhere] whitespace-pre-wrap">{token.token}</pre>
          </div>
          <div>
            <SectionTitle right={<Button size="xs" variant="ghost" onClick={() => copy('curl', curl)}>{copied === 'curl' ? <Check className="size-3" /> : <Copy className="size-3" />}Copy</Button>}>Fire it</SectionTitle>
            <pre className="overflow-x-auto rounded-lg bg-base p-3 font-mono text-[12px] text-ink-2 ring-1 ring-line/60 ring-inset whitespace-pre-wrap [overflow-wrap:anywhere]">{curl}</pre>
            <p className="mt-1.5 text-[12px] text-dim">The token also works as <Mono>X-NeuroCode-Token</Mono> or <Mono>?token=</Mono> for senders that cannot set a header.</p>
          </div>
        </div>
        <DialogFooter><Button onClick={onClose}>Done</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

const PRESETS: { value: PresetId; label: string }[] = [
  { value: 'daily', label: 'Daily' },
  { value: 'weekdays', label: 'Weekdays' },
  { value: 'weekly', label: 'Weekly' },
  { value: 'hourly', label: 'Hourly' },
  { value: 'custom', label: 'Custom cron' },
  { value: 'none', label: 'Only on demand' },
];
const pad = (n: number) => String(n).padStart(2, '0');

function Editor({ initial, projects, onClose, onSaved }: {
  initial: Routine | null; projects: Project[]; onClose: () => void; onSaved: (r: Routine) => void;
}) {
  const { project } = useProject();
  const [name, setName] = useState(initial?.name ?? '');
  const [projectId, setProjectId] = useState(initial?.projectId ?? projects.find((p) => p.id === project?.id)?.id ?? projects[0]?.id ?? '');
  const [what, setWhat] = useState<'requirement' | 'workflow'>(initial?.what ?? 'requirement');
  const [workflowId, setWorkflowId] = useState(initial?.workflowId ?? '');
  const [requirement, setRequirement] = useState(initial?.requirement ?? '');
  const [preset, setPreset] = useState<Preset>(() => readPreset(initial ? initial.cadence : '0 9 * * 1-5'));
  const [custom, setCustom] = useState(initial?.cadence ?? '');
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{ cron: string; data: CadencePreview | null; error: string | null } | null>(null);

  const flows = useRemote(projectId ? `routines:workflows:${projectId}` : null, () => workflowsApi.list(projectId));
  const choices = useMemo(() => (flows.data ?? []).filter((w) => !w.builtin && (!w.projectId || w.projectId === projectId)), [flows.data, projectId]);
  // A workflow that does not run in the chosen project is never sent: the first one that does is.
  const chosen = choices.some((w) => w.id === workflowId) ? workflowId : choices[0]?.id ?? '';

  const cron = preset.id === 'custom' ? custom.trim() : presetCron(preset);
  // What the cadence means, from the server that will fire it: in words, and its next three minutes.
  useEffect(() => {
    let live = true;
    const t = window.setTimeout(() => {
      routinesApi.cadence(cron).then(
        (data) => { if (live) setPreview({ cron, data, error: null }); },
        (e: unknown) => { if (live) setPreview({ cron, data: null, error: reason(e, 'The local API did not answer.') }); },
      );
    }, 300);
    return () => { live = false; window.clearTimeout(t); };
  }, [cron]);
  const read = preview?.cron === cron ? preview : null;

  const time = `${pad(preset.hour)}:${pad(preset.minute)}`;
  const setTime = (v: string) => {
    const [h, m] = v.split(':').map(Number);
    if (Number.isFinite(h) && Number.isFinite(m)) setPreset((p) => ({ ...p, hour: h, minute: m }));
  };
  const valid = name.trim() && projectId && requirement.trim().length >= 3 && (what === 'requirement' || chosen) && !read?.error && (preset.id !== 'custom' || cron);

  const save = async () => {
    setBusy(true);
    const body: RoutineInput = {
      name: name.trim(), projectId, workflowId: what === 'workflow' ? chosen : null,
      requirement: requirement.trim(), cadence: cron, enabled: initial?.enabled ?? true,
    };
    try {
      const saved = initial ? await routinesApi.update(initial.id, body) : await routinesApi.create(body);
      toast.success(initial ? `${saved.name} saved` : `${saved.name} created`, {
        description: saved.nextAt ? `Next fire ${utc(saved.nextAt)}.` : 'It fires on Run now or its webhook.',
      });
      onSaved(saved);
    } catch (e) {
      toast.error('Not saved', { description: reason(e, 'The local API did not answer.') });
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o && !busy) onClose(); }}>
      <DialogContent className="sm:max-w-[620px]">
        <DialogHeader>
          <DialogTitle>{initial ? `Edit ${initial.name}` : 'New routine'}</DialogTitle>
          <DialogDescription>
            On schedule and from its webhook it acts as {initial?.createdBy && initial ? initial.createdBy : 'you'}, with the permissions held at that moment. Saving it does not fire it.
          </DialogDescription>
        </DialogHeader>
        <div className="grid max-h-[64vh] min-w-0 gap-3 overflow-y-auto pr-1">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Name" value={name} onChange={setName} placeholder="Nightly dependency check" autoFocus />
            <SelectField label="Project" value={projectId} onChange={setProjectId} options={projects.map((p) => ({ value: p.id, label: p.name }))} />
          </div>
          <div>
            <span className="mb-1.5 block text-[12.5px] font-medium text-soft">Each fire</span>
            <Segmented options={[{ id: 'requirement', label: 'Compiles a requirement' }, { id: 'workflow', label: 'Runs a workflow' }]} value={what} onChange={setWhat} />
          </div>
          {what === 'workflow' && (
            choices.length ? (
              <SelectField label="Workflow" value={chosen} onChange={setWorkflowId} options={choices.map((w) => ({ value: w.id, label: w.name }))} />
            ) : (
              <p className="rounded-lg bg-surface-2/60 px-3 py-2 text-[13px] text-soft">
                {flows.loading ? 'Loading workflows…' : 'No workflow runs in this project yet. Write one on the Workflows screen, or compile a requirement instead.'}
              </p>
            )
          )}
          <label className="block">
            <span className="mb-1.5 block text-[12.5px] font-medium text-soft">{what === 'workflow' ? 'Input — what {input} becomes on each run' : 'Requirement'}</span>
            <textarea
              value={requirement} onChange={(e) => setRequirement(e.target.value)} rows={4}
              placeholder={what === 'workflow' ? 'the weekly tax report' : 'Update the dependencies with known vulnerabilities and keep the tests green'}
              className="focus-brand w-full resize-none rounded-lg border border-line bg-surface-2/60 p-3 text-[13.5px] leading-relaxed text-ink placeholder:text-dim focus-visible:outline-none"
            />
          </label>
          <div className="grid gap-3 sm:grid-cols-3">
            <SelectField label="Cadence" value={preset.id} onChange={(v) => {
              const id = v as PresetId;
              if (id === 'custom' && !custom.trim()) setCustom(presetCron(preset) || '0 9 * * 1-5');
              setPreset((p) => ({ ...p, id }));
            }} options={PRESETS} />
            {preset.id === 'hourly' && (
              <SelectField label="At minute" value={String(preset.minute)} onChange={(v) => setPreset((p) => ({ ...p, minute: Number(v) }))}
                options={Array.from({ length: 12 }, (_, i) => ({ value: String(i * 5), label: `:${pad(i * 5)}` }))} />
            )}
            {(preset.id === 'daily' || preset.id === 'weekdays' || preset.id === 'weekly') && (
              <Field label="At (UTC)" type="time" value={time} onChange={setTime} />
            )}
            {preset.id === 'weekly' && (
              <SelectField label="On" value={String(preset.weekday)} onChange={(v) => setPreset((p) => ({ ...p, weekday: Number(v) }))}
                options={DAY_NAMES.map((d, i) => ({ value: String(i), label: d }))} />
            )}
            {preset.id === 'custom' && (
              <Field className="sm:col-span-2" label="Cron (UTC)" value={custom} onChange={setCustom} mono placeholder="*/30 9-17 * * 1-5"
                hint="minute · hour · day of month · month · day of week" />
            )}
          </div>
          <div className="rounded-lg bg-surface-2/60 px-3 py-2.5 text-[13px]">
            {!read ? (
              <span className="flex items-center gap-2 text-dim"><Loader2 className="size-3.5 animate-spin" />Reading the cadence…</span>
            ) : read.error ? (
              <span className="text-danger">{read.error}</span>
            ) : (
              <>
                <div className="font-medium text-ink-2">{read.data?.label}</div>
                {!!read.data?.next.length && <div className="mt-0.5 text-dim">Next: {read.data.next.map(utc).join(' · ')}</div>}
              </>
            )}
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={() => void save()} disabled={busy || !valid}>
            {busy && <Loader2 className="size-3.5 animate-spin" />}{initial ? 'Save' : 'Create'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
