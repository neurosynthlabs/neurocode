import { useState, type SyntheticEvent, type ReactNode } from 'react';
import { KeyRound, Loader2, PlugZap } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Cell, DataTable, Dot, Field, KV, Mono, Page, PageBody, PageHeader, Panel, Row, Segmented, Tag } from '@/components/os';
import { api, type AiConfig, type AiPatch, type AiPreference, type AiTestResult, type CompilerInfo } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';
import { attempt, useAdmin } from './load';
import { DemoNote, LoadError, Loading } from './kit';

type ProviderId = CompilerInfo['provider'];
type Test = AiTestResult | 'running';

const DEMO_AI: AiConfig = {
  preference: 'auto', preferenceLocked: false, active: { provider: 'rules', model: 'offline planner' },
  deepseek: { hasKey: false, keyMask: null, keySource: null, model: 'deepseek-chat', baseUrl: 'https://api.deepseek.com', rejected: false },
  ollama: { url: 'http://127.0.0.1:11434', model: 'qwen2.5-coder:7b', ready: false },
};
const ROUTES: { id: AiPreference; label: string }[] = [
  { id: 'auto', label: 'Automatic' }, { id: 'deepseek', label: 'DeepSeek' }, { id: 'ollama', label: 'Ollama' }, { id: 'rules', label: 'Offline rules' },
];
const ROUTE_NOTE: Record<AiPreference, string> = {
  auto: 'DeepSeek when a key is set, then a local Ollama model when one is pulled, then the offline rules.',
  deepseek: 'Only DeepSeek. Without a working key, the offline rules answer instead.',
  ollama: 'Only the local Ollama model, so nothing leaves this machine.',
  rules: 'No model at all. Every feature answers with its offline rules, instantly and for free.',
};
const FEATURES = [
  ['Requirement compiler', 'A plan with steps, risks and open questions', 'A keyword planner that follows memory into the database'],
  ['Ask memory', 'An answer written from the facts, with citations', 'The matching facts, quoted'],
  ['Brainstorm', 'A sharp brief that argues against itself', 'A template that keeps your words'],
  ['Add from text', 'Durable facts picked out of notes and chats', 'Sentences that state a rule or a decision'],
];
const answering = (a: CompilerInfo) => (a.provider === 'rules' ? 'Offline rules' : a.model);

export default function AiProviders() {
  const { can } = useAuth();
  const { data: cfg, setData, error, live, reload } = useAdmin(api.admin.ai, DEMO_AI);
  const manage = live && can('workspace:admin');
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
        subtitle="Every AI feature goes through one gateway. With no key, offline rules answer and say so. Add a DeepSeek key or a local Ollama model whenever you are ready."
      />
      <PageBody>
        {!live && <DemoNote what="Keys and provider settings" />}
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
              <p className="mt-2.5 text-[13px] text-soft">{ROUTE_NOTE[cfg.preference]}</p>
              {cfg.preferenceLocked && (
                <p className="mt-1.5 text-[12.5px] text-dim">NEUROCODE_COMPILER is set where the API runs, and it wins over this choice.</p>
              )}
            </Panel>

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
                  Kept in server/secrets.json beside the database, readable only by the account that runs the API. Never sent back
                  in full, never logged.
                </p>
                <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <Field label="Model" mono value={ds.model ?? cfg.deepseek.model} onChange={(v) => setDs({ ...ds, model: v })} disabled={!manage} />
                  <Field label="Base URL" mono value={ds.baseUrl ?? cfg.deepseek.baseUrl} onChange={(v) => setDs({ ...ds, baseUrl: v })} disabled={!manage} />
                </div>
                <Footer
                  result={tests.deepseek} canTest={live} onTest={() => void test('deepseek')}
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
                  result={tests.ollama} canTest={live} onTest={() => void test('ollama')}
                  dirty={ol.url !== undefined || ol.model !== undefined}
                  onSave={async () => { if (await update({ ollamaUrl: ol.url, ollamaModel: ol.model }, 'Ollama settings saved')) setOl({}); }}
                />
              </Panel>
            </div>

            <Panel flush eyebrow="Always available" title="Offline rules">
              <DataTable head={['Feature', 'With a model', 'With no model']}>
                {FEATURES.map(([feature, model, rules]) => (
                  <Row key={feature}>
                    <Cell className="font-medium whitespace-nowrap text-ink">{feature}</Cell>
                    <Cell className="text-[13px] text-ink-2">{model}</Cell>
                    <Cell className="text-[13px] text-soft">{rules}</Cell>
                  </Row>
                ))}
              </DataTable>
            </Panel>
          </div>
        )}
      </PageBody>
    </Page>
  );
}

function Footer({ result, canTest, onTest, dirty, onSave, extra }: {
  result?: Test; canTest: boolean; onTest: () => void; dirty: boolean; onSave: () => void; extra?: ReactNode;
}) {
  return (
    <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line/60 pt-4">
      <TestLine result={result} />
      <div className="flex flex-wrap items-center gap-2">
        {extra}
        <Button size="sm" variant="outline" onClick={onTest} disabled={!canTest || result === 'running'}>
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
