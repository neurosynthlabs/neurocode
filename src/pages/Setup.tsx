import { useEffect, useState, type SyntheticEvent } from 'react';
import { ArrowLeft, ArrowRight, Check, KeyRound, Loader2, Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Field, SelectField } from '@/components/os';
import { LogoSymbol } from '@/components/os/Logo';
import { SignalGlow } from '@/components/os/Glow';
import { ApiError, api, type LaneId } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';

/* First run. The workspace has nobody yet, so the first person to arrive names it and becomes its
   Owner. AI is offered last and can wait: planning and brainstorming need a model, and a free key is
   enough; asking memory works without one. */

const STEPS = ['Workspace', 'Your account', 'AI'];
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
/** The free lanes a first key is most likely to be for. The rest are in Admin → AI providers. */
const FREE_LANES: { value: LaneId; label: string }[] = [
  { value: 'groq', label: 'Groq' }, { value: 'cerebras', label: 'Cerebras' }, { value: 'gemini', label: 'Gemini' },
];
const FEATURES = [
  ['Requirement compiler', 'turns a requirement into a plan with steps, risks and questions'],
  ['Ask memory', 'answers from what the workspace has learned, with its sources'],
  ['Brainstorm', 'expands an idea into a brief that argues against itself'],
  ['Add from text', 'picks durable facts out of meeting notes and chats'],
] as const;

function strength(p: string) {
  if (!p) return 'At least 10 characters. A short sentence is the easiest to remember.';
  if (p.length < 10) return `${10 - p.length} more ${10 - p.length === 1 ? 'character' : 'characters'} to go.`;
  return p.length < 16 ? 'Good.' : 'Strong.';
}

export default function Setup() {
  const { refresh } = useAuth();
  const [step, setStep] = useState(0);
  const [lane, setLane] = useState<LaneId>('groq');
  const [workspace, setWorkspace] = useState('');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [again, setAgain] = useState('');
  const [key, setKey] = useState('');
  const [token, setToken] = useState('');
  // A server reachable from the internet asks for its setup token before anyone may become its first Owner.
  const [needsToken, setNeedsToken] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const fail = (e: unknown) => setError(e instanceof ApiError ? e.message : 'The local API did not answer. Is it still running?');
  const accountReady = !!name.trim() && EMAIL.test(email.trim()) && password.length >= 10 && password === again;
  const workspaceReady = !!workspace.trim() && (!needsToken || !!token.trim());

  useEffect(() => {
    let live = true;
    api.authStatus().then(
      (s) => { if (live) setNeedsToken(!!s.setupNeedsToken); },
      (e: unknown) => console.error('[NeuroCode] GET /auth/status failed:', e),
    );
    return () => { live = false; };
  }, []);

  const create = async () => {
    setError('');
    setBusy(true);
    try {
      await api.setup({ workspace: workspace.trim(), name: name.trim(), email: email.trim(), password,
        ...(needsToken ? { setupToken: token.trim() } : {}) });
      setStep(2);
    } catch (e) {
      fail(e);
    }
    setBusy(false);
  };

  const finish = async (saveKey: boolean) => {
    setError('');
    setBusy(true);
    try {
      if (saveKey && key.trim()) await api.admin.updateAi({ lane, key: key.trim() });
      await refresh();  // the Owner is signed in already; this opens the workspace
    } catch (e) {
      fail(e);
      setBusy(false);
    }
  };

  const submit = (e: SyntheticEvent) => {
    e.preventDefault();
    if (busy) return;
    if (step === 0 && workspaceReady) setStep(1);
    else if (step === 1 && accountReady) void create();
    else if (step === 2) void finish(true);
  };

  return (
    <div className="relative flex h-full w-full justify-center overflow-y-auto bg-bg px-5 py-10">
      <SignalGlow />
      <form onSubmit={submit} className="relative my-auto w-full max-w-[460px]">
        <div className="flex flex-col items-center text-center">
          <div className="nc-icon-in">
            <LogoSymbol size={64} title="NeuroCode" />
          </div>
          <h1 className="mt-6 text-[27px] leading-[1.15] font-semibold tracking-[-0.028em] text-ink">Welcome to NeuroCode</h1>
          <p className="mt-2 max-w-sm text-[14.5px] leading-relaxed text-soft">
            Three short steps and your workspace is ready. It lives in your own database; a model sees only what a step sends it.
          </p>
        </div>

        <ol className="mt-8 flex flex-wrap items-center justify-center gap-2" aria-label="Setup steps">
          {STEPS.map((s, i) => (
            <li key={s} className="flex items-center gap-2" aria-current={i === step ? 'step' : undefined}>
              <span className={cn('grid size-6 place-items-center rounded-full text-[12px] font-semibold',
                i < step ? 'bg-ok/15 text-ok' : i === step ? 'bg-brand text-brand-ink' : 'bg-surface-3 text-dim')}>
                {i < step ? <Check className="size-3.5" strokeWidth={2.6} /> : i + 1}
              </span>
              <span className={cn('text-[13px]', i === step ? 'font-medium text-ink' : 'text-dim')}>{s}</span>
              {i < STEPS.length - 1 && <span className="mx-1 hidden h-px w-6 bg-line sm:block" />}
            </li>
          ))}
        </ol>

        <div className="panel mt-6 rounded-2xl border border-line/70 bg-surface p-6">
          {step === 0 && (
            <>
              <h2 className="text-[16px] font-semibold text-ink">Name your workspace</h2>
              <p className="mt-1 text-[13.5px] text-soft">Usually your company or team. You can rename it later in Admin → Workspace.</p>
              <Field className="mt-4" label="Workspace name" value={workspace} onChange={setWorkspace} placeholder="Acme Engineering" autoFocus />
              {needsToken && (
                <Field className="mt-3.5" label="Setup token" value={token} onChange={setToken} mono autoComplete="off"
                  hint="This server asks for it once, before its first Owner is made. It is NEUROCODE_SETUP_TOKEN in the server's .env." />
              )}
              <div className="mt-6 flex justify-end">
                <Button type="submit" disabled={!workspaceReady}>Continue<ArrowRight className="size-3.5" /></Button>
              </div>
            </>
          )}

          {step === 1 && (
            <>
              <h2 className="text-[16px] font-semibold text-ink">Create the Owner account</h2>
              <p className="mt-1 text-[13.5px] text-soft">
                The Owner can do everything, including adding people and setting keys. You can add more Owners later.
              </p>
              <p className="mt-2 text-[12.5px] leading-relaxed text-dim">
                This account is made here and nowhere else. If your team signs in through Google, Okta or Entra, the
                Owner turns that on afterwards in Admin → Workspace — an identity provider can never make the
                first Owner of a workspace.
              </p>
              <div className="mt-4 space-y-3">
                <Field label="Your name" value={name} onChange={setName} autoComplete="name" autoFocus />
                <Field label="Email" type="email" value={email} onChange={setEmail} autoComplete="username" placeholder="you@company.com" />
                <Field label="Password" type="password" value={password} onChange={setPassword} autoComplete="new-password" hint={strength(password)} />
                <Field label="Password, again" type="password" value={again} onChange={setAgain} autoComplete="new-password"
                  hint={again && again !== password ? 'The two do not match yet.' : undefined} />
              </div>
              <div className="mt-6 flex items-center justify-between gap-2">
                <Button type="button" variant="ghost" onClick={() => setStep(0)}><ArrowLeft className="size-3.5" />Back</Button>
                <Button type="submit" disabled={!accountReady || busy}>
                  {busy ? <><Loader2 className="size-3.5 animate-spin" />Creating…</> : <>Create workspace<ArrowRight className="size-3.5" /></>}
                </Button>
              </div>
            </>
          )}

          {step === 2 && (
            <>
              <h2 className="text-[16px] font-semibold text-ink">AI, now or later</h2>
              <p className="mt-1 text-[13.5px] leading-relaxed text-soft">
                Planning and brainstorming need a model; asking memory works without one, by quoting the facts that match.
                A free key from Groq, Cerebras or Gemini takes a minute — add it now, or any time in Admin → AI providers.
              </p>
              <ul className="mt-4 space-y-2">
                {FEATURES.map(([title, text]) => (
                  <li key={title} className="flex gap-2.5 text-[13px] leading-snug">
                    <Sparkles className="mt-0.5 size-3.5 shrink-0 text-brand" />
                    <span><span className="font-medium text-ink">{title}</span> <span className="text-soft">— {text}</span></span>
                  </li>
                ))}
              </ul>
              <SelectField className="mt-5" label="Provider" value={lane} onChange={(v) => setLane(v as LaneId)} options={FREE_LANES} />
              <Field
                className="mt-3" label="API key (optional)" type="password" mono value={key} onChange={setKey}
                icon={<KeyRound className="size-3.5" />} autoComplete="off"
                hint="Kept on this machine, readable only by the account that runs NeuroCode. Never shown again in full."
              />
              <div className="mt-6 flex items-center justify-between gap-2">
                <Button type="button" variant="ghost" onClick={() => void finish(false)} disabled={busy}>Skip for now</Button>
                <Button type="submit" disabled={busy}>
                  {busy ? <Loader2 className="size-3.5 animate-spin" /> : null}
                  {key.trim() ? 'Save key and open' : 'Open workspace'}<ArrowRight className="size-3.5" />
                </Button>
              </div>
            </>
          )}

          {error && <p role="alert" className="mt-4 text-[13px] text-danger">{error}</p>}
        </div>
      </form>
    </div>
  );
}
