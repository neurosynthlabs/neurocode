import { useState, type SyntheticEvent, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { fetchModels } from '@/lib/live/models';
import { FEATURE_LABEL } from '@/lib/live/usage';
import { useRemote } from '@/lib/remote';
import { tokens } from '@/pages/code/format';
import { KeyRound, Loader2, PlugZap } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Cell, DataTable, Dot, Field, KV, Mono, Page, PageBody, PageHeader, Panel, Row, Segmented, Tag } from '@/components/os';
import { api, type AiConfig, type AiLane, type AiPatch, type AiPreference, type AiTestResult, type CompilerInfo, type LaneId } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';
import { attempt, useAdmin } from './load';
import { LoadError, Loading } from './kit';

type ProviderId = CompilerInfo['provider'];
type Test = AiTestResult | 'running';

const ROUTES: { id: AiPreference; label: string }[] = [
  { id: 'auto', label: 'Automatic' }, { id: 'free', label: 'Free only' }, { id: 'local', label: 'This Mac only' }, { id: 'rules', label: 'No model' },
];
const answering = (a: CompilerInfo) => (a.provider === 'rules' ? 'No model' : a.model);

export default function AiProviders() {
  const { can } = useAuth();
  const { data: cfg, setData, error, reload } = useAdmin<AiConfig>(api.admin.ai);
  // What each routing choice allows, and what each feature does without a lane, in the gateway's own words.
  const models = useRemote('models', fetchModels);
  const manage = can('workspace:admin');
  const [key, setKey] = useState('');
  const [ds, setDs] = useState<{ model?: string; baseUrl?: string }>({});
  const [ol, setOl] = useState<{ url?: string; model?: string }>({});
  const [busy, setBusy] = useState(false);
  const [tests, setTests] = useState<Partial<Record<ProviderId, Test>>>({});

  const update = async (patch: AiPatch, done: string) => {
    setBusy(true);
    const next = await attempt(() => api.admin.updateAi(patch), 'Not saved');
    setBusy(false);
    if (next) { setData(next); toast.success(done); }
    return !!next;
  };
  const saveKey = async (e: SyntheticEvent) => {
    e.preventDefault();
    if (key.trim() && (await update({ deepseekKey: key.trim() }, 'Key saved. From now on it is only shown masked.'))) setKey('');
  };
  const test = async (provider: ProviderId) => {
    setTests((t) => ({ ...t, [provider]: 'running' }));
    const r = await attempt(() => api.admin.testAi(provider), 'The test did not run');
    setTests((t) => ({ ...t, [provider]: r ?? undefined }));
    if (r) reload();  // a refused key is remembered by the gateway: show it
  };

  return (
    <Page>
      <PageHeader
        title="AI providers"
        subtitle="Every AI feature goes through one gateway. Add a free key (Groq, Cerebras or Gemini take a minute) or a local Ollama model; with none, the features that need a model say so."
      />
      <PageBody>
        {error ? <LoadError error={error} onRetry={reload} /> : !cfg ? <Loading /> : (
          <div className="space-y-5">
            <Panel
              className="accent-top" eyebrow="Answering now"
              title={
                <span className="flex items-center gap-2.5">
                  <Dot state={cfg.active.provider === 'rules' ? 'idle' : 'ok'} pulse={cfg.active.provider !== 'rules'} />
                  {answering(cfg.active)}
                </span>
              }
            >
              {cfg.active.note && <p className="mb-3 text-[13px] text-warn">{cfg.active.note}</p>}
              <div className={cn(!manage || cfg.preferenceLocked ? 'pointer-events-none opacity-60' : '')}>
                <Segmented
                  options={ROUTES} value={cfg.preference}
                  onChange={(p) => void update({ preference: p }, `Routing: ${ROUTES.find((r) => r.id === p)?.label}`)}
                />
              </div>
              {models.data && (
                <p className="mt-2.5 text-[13px] text-soft">
                  {cfg.preference in models.data.preferences
                    ? models.data.preferences[cfg.preference as keyof typeof models.data.preferences]
                    : `Pinned to ${cfg.preference}.`}
                </p>
              )}
              <p className="mt-1.5 text-[12.5px] text-dim">
                {cfg.active.lanes ? `${cfg.active.lanes} ${cfg.active.lanes === 1 ? 'lane can' : 'lanes can'} answer right now.` : 'No lane can answer right now.'}
              </p>
              {cfg.preferenceLocked && (
                <p className="mt-1.5 text-[12.5px] text-dim">NEUROCODE_COMPILER is set where the API runs, and it wins over this choice.</p>
              )}
            </Panel>

            <Lanes lanes={cfg.lanes} manage={manage} busy={busy} tests={tests} onTest={(id) => void test(id)}
              onPatch={(patch, done) => update(patch, done)} />

            <Usage />

            <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
              <Panel eyebrow="Cloud model" title="DeepSeek">
                <KV
                  k="API key"
                  v={cfg.deepseek.hasKey ? (
                    <span className="inline-flex max-w-full items-center gap-2">
                      <Mono>{cfg.deepseek.keyMask}</Mono>
                      <Tag tone={cfg.deepseek.rejected ? 'danger' : 'ok'}>
                        {cfg.deepseek.rejected ? 'Rejected' : cfg.deepseek.keySource === 'environment' ? 'Environment' : 'Saved'}
                      </Tag>
                    </span>
                  ) : <Tag>Not set</Tag>}
                />
                <KV k="Model" v={cfg.deepseek.model} mono />
                <form onSubmit={saveKey} className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-end">
                  <Field
                    className="flex-1" label={cfg.deepseek.hasKey ? 'Replace the key' : 'API key'} type="password" mono
                    value={key} onChange={setKey} placeholder="sk-…" autoComplete="off" disabled={!manage}
                    icon={<KeyRound className="size-3.5" />}
                  />
                  <Button type="submit" disabled={!manage || busy || !key.trim()}>Save key</Button>
                </form>
                <p className="mt-2 text-[12px] leading-relaxed text-dim">
                  Kept in the API’s keys file, readable only by the account that runs the API. Never sent back in full, never
                  logged.
                </p>
                <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <Field label="Model" mono value={ds.model ?? cfg.deepseek.model} onChange={(v) => setDs({ ...ds, model: v })} disabled={!manage} />
                  <Field label="Base URL" mono value={ds.baseUrl ?? cfg.deepseek.baseUrl} onChange={(v) => setDs({ ...ds, baseUrl: v })} disabled={!manage} />
                </div>
                <Footer
                  result={tests.deepseek} onTest={() => void test('deepseek')}
                  dirty={ds.model !== undefined || ds.baseUrl !== undefined}
                  onSave={async () => { if (await update({ deepseekModel: ds.model, deepseekUrl: ds.baseUrl }, 'DeepSeek settings saved')) setDs({}); }}
                  extra={manage && cfg.deepseek.hasKey && cfg.deepseek.keySource === 'workspace' && (
                    <Button size="sm" variant="ghost" className="text-danger hover:text-danger" disabled={busy}
                      onClick={() => void update({ deepseekKey: '' }, 'Key removed')}>Remove key</Button>
                  )}
                />
              </Panel>

              <Panel eyebrow="Local model" title="Ollama">
                <KV
                  k="Status"
                  v={<span className="inline-flex items-center gap-2"><Dot state={cfg.ollama.ready ? 'ok' : 'idle'} />{cfg.ollama.ready ? 'Answering' : 'Not reachable'}</span>}
                />
                <KV k="Model" v={cfg.ollama.model} mono />
                <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <Field label="Server URL" mono value={ol.url ?? cfg.ollama.url} onChange={(v) => setOl({ ...ol, url: v })} disabled={!manage} />
                  <Field label="Model" mono value={ol.model ?? cfg.ollama.model} onChange={(v) => setOl({ ...ol, model: v })} disabled={!manage} />
                </div>
                <p className="mt-2 text-[12px] leading-relaxed text-dim">
                  Free and private: nothing leaves this machine. Install Ollama, then run <Mono>ollama pull {cfg.ollama.model}</Mono>.
                </p>
                <Footer
                  result={tests.ollama} onTest={() => void test('ollama')}
                  dirty={ol.url !== undefined || ol.model !== undefined}
                  onSave={async () => { if (await update({ ollamaUrl: ol.url, ollamaModel: ol.model }, 'Ollama settings saved')) setOl({}); }}
                />
              </Panel>
            </div>

            {models.data && (
              <Panel flush eyebrow="What each feature asks the gateway for" title="When no lane answers">
                <DataTable head={['Feature', 'Without a model', 'How it routes']}>
                  {models.data.routes.map((r) => (
                    <Row key={r.feature}>
                      <Cell className="font-medium whitespace-nowrap text-ink">{FEATURE_LABEL[r.feature] ?? r.feature}</Cell>
                      <Cell><Tag tone={r.offline ? 'ok' : 'neutral'}>{r.offline ? 'Answers offline' : 'Needs a model'}</Tag></Cell>
                      <Cell className="text-[13px] text-soft">{r.how}</Cell>
                    </Row>
                  ))}
                </DataTable>
              </Panel>
            )}
          </div>
        )}
      </PageBody>
    </Page>
  );
}

/** Every lane, and whether it can answer. A lane is a provider and a model together. */
function Lanes({ lanes, manage, busy, tests, onTest, onPatch }: {
  lanes: AiLane[]; manage: boolean; busy: boolean; tests: Partial<Record<ProviderId, Test>>;
  onTest: (id: LaneId) => void; onPatch: (patch: AiPatch, done: string) => Promise<boolean>;
}) {
  const [keys, setKeys] = useState<Partial<Record<LaneId, string>>>({});
  if (lanes.length === 0) return null;
  const ready = lanes.filter((l) => l.ready).length;
  const save = async (lane: AiLane) => {
    const key = (keys[lane.id] ?? '').trim();
    if (key && (await onPatch({ lane: lane.id, key }, `${lane.label} key saved`))) setKeys((k) => ({ ...k, [lane.id]: '' }));
  };

  return (
    <Panel
      flush title="Lanes" eyebrow={`${ready} of ${lanes.length} can answer · free first, paid only when the free ones are spent`}
    >
      <div className="divide-y divide-line/60">
        {lanes.map((lane) => (
          <div key={lane.id} className="px-5 py-3.5">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <Dot state={lane.ready ? 'ok' : lane.rejected ? 'error' : 'idle'} />
              <span className="text-[13.5px] font-medium text-ink">{lane.label}</span>
              <Tag tone={lane.free ? 'ok' : 'neutral'}>{lane.free ? 'Free' : 'Paid'}</Tag>
              <Mono>{lane.model}</Mono>
              <span className="ml-auto flex items-center gap-2">
                {lane.rpd > 0 && <span className="tnum text-[12px] text-dim">{lane.spent.today}/{lane.rpd} today</span>}
                {lane.hasKey && (
                  <Tag tone={lane.rejected ? 'danger' : 'ok'}>
                    {lane.rejected ? 'Key refused' : lane.keySource === 'environment' ? 'Key in environment' : 'Key saved'}
                  </Tag>
                )}
                <Button size="sm" variant="outline" disabled={!manage || tests[lane.id] === 'running'} onClick={() => onTest(lane.id)}>
                  {tests[lane.id] === 'running' ? <Loader2 className="size-3.5 animate-spin" /> : <PlugZap className="size-3.5" />}Test
                </Button>
              </span>
            </div>

            <p className="mt-1.5 text-[12.5px] leading-relaxed text-dim">
              {lane.ready ? lane.note : <span className="text-warn">{lane.blocked}</span>}
              {' · '}good at {lane.goodAt.join(', ')}
            </p>

            {manage && lane.needsKey && !lane.hasKey && (
              <div className="mt-2.5 flex flex-col gap-2 sm:flex-row sm:items-end">
                <Field
                  className="flex-1" label={`${lane.label} key`} type="password" mono autoComplete="off" placeholder="paste it here"
                  value={keys[lane.id] ?? ''} onChange={(v) => setKeys((k) => ({ ...k, [lane.id]: v }))}
                  icon={<KeyRound className="size-3.5" />}
                />
                <Button size="sm" disabled={busy || !(keys[lane.id] ?? '').trim()} onClick={() => void save(lane)}>Save</Button>
              </div>
            )}
            {lane.signup && (!lane.needsKey || !lane.hasKey) && (
              <p className="mt-1.5 text-[12px] text-soft">{lane.needsKey ? 'Free key: ' : 'Install: '}<Mono>{lane.signup}</Mono></p>
            )}
            {manage && lane.hasKey && lane.keySource === 'workspace' && (
              <div className="mt-2 flex gap-2">
                <Button size="sm" variant="ghost" className="text-danger hover:text-danger" disabled={busy}
                  onClick={() => void onPatch({ lane: lane.id, key: '' }, `${lane.label} key removed`)}>Remove</Button>
                <Button size="sm" variant="ghost" disabled={busy}
                  onClick={() => void onPatch({ lane: lane.id, enabled: !lane.enabled }, `${lane.label} ${lane.enabled ? 'switched off' : 'switched on'}`)}>
                  {lane.enabled ? 'Switch off' : 'Switch on'}
                </Button>
              </div>
            )}
          </div>
        ))}
      </div>
    </Panel>
  );
}

/** What each provider did over the last 30 days, from the gateway's ledger. */
function Usage() {
  const u = useRemote('usage:30', () => api.usage(30));
  if (!u.data) return null;
  const { totals: t, byProvider } = u.data;
  return (
    <Panel flush title="Last 30 days" eyebrow={`${t.calls} calls · ${t.modelCalls} to a model · ${t.failures} failed`}
      actions={<Link to="/cost" className="text-[12.5px] text-brand hover:underline">Full ledger →</Link>}>
      {byProvider.length === 0 ? <p className="px-5 py-3 text-[13px] text-soft">No AI call yet.</p> : (
        <DataTable head={['Answered by', 'Calls', 'Failed', 'Tokens', 'Average']}>
          {byProvider.map((p) => (
            <Row key={`${p.provider}:${p.model}`}>
              <Cell className="font-medium text-ink">{p.provider === 'rules' ? `Offline · ${p.model}` : p.model}</Cell>
              <Cell className="tnum">{p.calls}</Cell>
              <Cell className={cn('tnum', p.failures > 0 && 'text-warn')}>{p.failures || '—'}</Cell>
              <Cell className="tnum">{p.provider === 'rules' ? 'free' : tokens(p.tokensIn + p.tokensOut)}</Cell>
              <Cell className="tnum">{p.avgMs} ms</Cell>
            </Row>
          ))}
        </DataTable>
      )}
    </Panel>
  );
}

function Footer({ result, onTest, dirty, onSave, extra }: {
  result?: Test; onTest: () => void; dirty: boolean; onSave: () => void; extra?: ReactNode;
}) {
  return (
    <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line/60 pt-4">
      <TestLine result={result} />
      <div className="flex flex-wrap items-center gap-2">
        {extra}
        <Button size="sm" variant="outline" onClick={onTest} disabled={result === 'running'}>
          {result === 'running' ? <Loader2 className="size-3.5 animate-spin" /> : <PlugZap className="size-3.5" />}Test connection
        </Button>
        {dirty && <Button size="sm" onClick={onSave}>Save</Button>}
      </div>
    </div>
  );
}

function TestLine({ result }: { result?: Test }) {
  if (!result) return <span className="text-[12.5px] text-dim">Not tested yet</span>;
  if (result === 'running') return <span className="text-[12.5px] text-soft">Asking it to answer…</span>;
  return (
    <span className={cn('flex min-w-0 items-center gap-2 text-[12.5px]', result.ok ? 'text-ok' : 'text-danger')}>
      <Dot state={result.ok ? 'ok' : 'error'} />
      <span className="min-w-0 truncate" title={result.detail}>{result.detail}{result.ok ? ` · ${result.ms} ms` : ''}</span>
    </span>
  );
}
