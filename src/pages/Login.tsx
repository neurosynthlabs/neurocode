import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, KeyRound, Loader2, ShieldCheck, Terminal } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Field, Tag, Mono, LogoMark, Ascii, Dot } from '@/components/os';
import { useTheme, THEMES } from '@/lib/theme';
import { cn } from '@/lib/utils';

const PITCH = `  YOU  ──▶  COMMANDER  ──▶  ORCHESTRATOR  ──┬──▶  ARCHITECT
   AI PM      compiles        assigns          ├──▶  BACKEND · FRONTEND · DB
   final      the ask         worktrees        ├──▶  QA · SECURITY
   approval                                    └──▶  REVIEWER  ──▶  YOU APPROVE`;

const FACTS = [
  ['2.4M', 'lines under management'],
  ['12', 'specialist agents'],
  ['71%', 'served by local models'],
  ['248', 'palettes, one accent each'],
];

export default function Login() {
  const nav = useNavigate();
  const { theme, setTheme } = useTheme();
  const [email, setEmail] = useState('engineering@sofscript.com');
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);

  const go = () => {
    setBusy(true);
    window.setTimeout(() => nav('/'), 700);
  };

  return (
    <div className="grid h-full w-full grid-cols-1 overflow-hidden bg-bg lg:grid-cols-[1.1fr_1fr]">
      {/* ── Brand side ───────────────────────────────────────── */}
      <aside className="rail-surface relative hidden min-h-0 flex-col justify-between overflow-hidden p-10 lg:flex">
        <div className="grid-lines pointer-events-none absolute inset-0 opacity-[0.35]" />
        <div className="relative">
          <span className="flex items-center gap-3">
            <LogoMark size={44} />
            <span className="leading-none">
              <span className="block text-[22px] tracking-[-0.025em]">
                <span className="font-bold" style={{ color: 'var(--rail-ink)' }}>Neuro</span>
                <span className="font-semibold" style={{ color: 'var(--rail-accent)' }}>Code</span>
              </span>
              <span className="mt-1.5 block font-mono text-[11px]" style={{ color: 'var(--rail-dim)' }}>
                AI engineering OS · v0.9.4 · self-hosted
              </span>
            </span>
          </span>

          <h1 className="mt-10 max-w-lg text-[27px] leading-[1.25] font-semibold tracking-[-0.028em]" style={{ color: 'var(--rail-ink)' }}>
            One operator.<br />A whole engineering organisation.
          </h1>
          <p className="mt-4 max-w-md text-[13.5px] leading-relaxed" style={{ color: 'var(--rail-soft)' }}>
            You write the requirement — in whatever language it arrives in. The OS compiles it, maps the blast radius
            against a parsed call graph, splits it across agents in isolated worktrees, tests it for real, reviews it
            against your own conventions, and stops at your signature.
          </p>
        </div>

        <div className="relative">
          <Ascii className="border-0 bg-transparent p-0 text-[10.5px]" >{PITCH}</Ascii>
        </div>

        <div className="relative">
          <div className="grid grid-cols-4 gap-6 border-t pt-6" style={{ borderColor: 'var(--rail-line)' }}>
            {FACTS.map(([v, l]) => (
              <div key={l}>
                <div className="figure text-[21px]" style={{ color: 'var(--rail-ink)' }}>{v}</div>
                <div className="mt-1 text-[10.5px] tracking-[0.04em] uppercase" style={{ color: 'var(--rail-dim)' }}>{l}</div>
              </div>
            ))}
          </div>
          <p className="mt-5 flex items-center gap-1.5 text-[11px]" style={{ color: 'var(--rail-dim)' }}>
            <ShieldCheck className="size-3.5" />
            Runs on your machine. Source, secrets and customer data never leave it.
          </p>
        </div>
      </aside>

      {/* ── Form side ────────────────────────────────────────── */}
      <main className="flex min-h-0 items-center justify-center overflow-y-auto px-6 py-10">
        <div className="w-full max-w-[380px]">
          <div className="mb-8 flex items-center gap-2.5 lg:hidden">
            <LogoMark size={34} />
            <span className="text-[17px] tracking-[-0.02em]">
              <span className="font-bold text-ink">Neuro</span><span className="font-semibold text-brand">Code</span>
            </span>
          </div>

          <h2 className="text-[19px] font-semibold tracking-[-0.02em] text-ink">Sign in</h2>
          <p className="mt-1 text-[12.5px] text-soft">
            This instance is single-operator. There is one seat, and it is yours.
          </p>

          <div className="mt-6 space-y-3">
            <Field label="Operator email" value={email} onChange={setEmail} placeholder="you@company.com" />
            <Field label="Workspace key" value={key} onChange={setKey} mono type="password"
              icon={<KeyRound className="size-3.5" />} placeholder="nc_live_••••••••••••••••" />

            <Button size="lg" className="w-full" disabled={busy} onClick={go}>
              {busy ? <><Loader2 className="size-3.5 animate-spin" />Opening workspace…</> : <>Enter workspace<ArrowRight className="size-3.5" /></>}
            </Button>

            <div className="flex items-center gap-3 py-1">
              <span className="h-px flex-1 bg-line" />
              <span className="eyebrow">or</span>
              <span className="h-px flex-1 bg-line" />
            </div>

            <Button size="lg" variant="outline" className="w-full" onClick={go}>
              <svg viewBox="0 0 16 16" className="size-3.5" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.4 7.4 0 0 1 2-.27c.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z"/></svg>Continue with GitHub
            </Button>
            <Button size="lg" variant="ghost" className="w-full" onClick={go}>
              <Terminal className="size-3.5" />Attach a running CLI session
            </Button>
          </div>

          <div className="mt-7 rounded-md border border-line bg-surface p-3.5">
            <div className="flex items-center gap-2">
              <Dot state="ok" pulse />
              <span className="text-[12px] font-medium text-ink">Local runtime detected</span>
            </div>
            <div className="mt-2 space-y-1 text-[11px] text-dim">
              <p className="flex items-center justify-between"><span>Orchestrator</span><Mono>127.0.0.1:8787</Mono></p>
              <p className="flex items-center justify-between"><span>Vector store</span><Mono>qdrant · local</Mono></p>
              <p className="flex items-center justify-between"><span>Models ready</span><Mono>9 of 14</Mono></p>
            </div>
          </div>

          <div className="mt-6">
            <div className="eyebrow mb-2">Preview a theme</div>
            <div className="flex flex-wrap gap-1.5">
              {THEMES.slice(0, 8).map((p) => (
                <button key={p.id} onClick={() => setTheme(p.id)} title={`${p.name} — ${p.note}`}
                  className={cn('flex overflow-hidden rounded-xs border transition-transform hover:scale-110',
                    theme === p.id ? 'border-brand' : 'border-line-strong')}>
                  {p.swatch.map((c, i) => <span key={i} className="size-4" style={{ background: c }} />)}
                </button>
              ))}
            </div>
          </div>

          <p className="mt-7 flex items-center gap-1.5 text-[11px] text-dim">
            <Tag tone="neutral">prototype</Tag>
            Any credentials open the workspace — this build carries no backend by design.
          </p>
        </div>
      </main>
    </div>
  );
}
