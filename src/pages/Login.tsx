import { useEffect, useState, type KeyboardEvent, type SyntheticEvent } from 'react';
import { ArrowRight, KeyRound, Loader2 } from 'lucide-react';
import { LogoSymbol } from '@/components/os/Logo';
import { SignalGlow } from '@/components/os/Glow';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';

/* The sign-in screen, drawn the way a Mac asks for a password: one centred column, the app's icon, and
   both fields in a single rounded group with the go button inside the password field. Everything is a
   theme token except the mark's marigold — the mark, the light behind it, the focus ring and the go button —
   which looks the same in every theme.

   A workspace with single sign-on gets one more thing: the provider's button, first, because that is
   the way in that workspace expects. When the workspace *requires* it, the password fields are not
   drawn at all — there is one line saying an Owner can still use a password, and a link that puts the
   fields back, because an Owner arrives at this screen on the day the provider is the broken thing.

   A sign-in that failed at the provider comes back as `?ssoError=…` on the address, because the
   browser was sent here by somebody else and there is no answer to read. It is lifted off the address
   as soon as it is read, so a reload does not show a stale refusal. */

/** The provider's refusal, read off the address. Reading it is pure; taking it off the address is
    the effect below, so the same refusal is never shown twice after a reload. */
const ssoRefusal = () => new URLSearchParams(window.location.search).get('ssoError') ?? '';

export default function Login() {
  const { workspace, login, sso, signInWithSso } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [error, setError] = useState('');
  // Passwords are hidden when the workspace requires SSO, and shown again the moment an Owner asks.
  const [withPassword, setWithPassword] = useState(false);
  // Read once, at the first render, and then lifted off the address so a reload starts clean.
  const [cameBack] = useState(ssoRefusal);
  const [dismissed, setDismissed] = useState(false);
  useEffect(() => {
    if (!cameBack) return;
    const here = new URL(window.location.href);
    here.searchParams.delete('ssoError');
    window.history.replaceState({}, '', here.pathname + here.search + here.hash);
  }, [cameBack]);
  const shown = error || (dismissed ? '' : cameBack);
  // Bumped on each refusal so the shake plays again for a second wrong password, not just the first.
  const [refusals, setRefusals] = useState(0);
  const [capsLock, setCapsLock] = useState(false);
  const ready = !!email.trim() && !!password && !busy;

  const submit = async (e: SyntheticEvent) => {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError('');
    setDismissed(true);          // their own attempt replaces whatever the provider said on the way back
    try {
      await login(email.trim(), password); // on success the workspace replaces this screen
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The local API did not answer. Is it still running?');
      setRefusals((n) => n + 1);
      setPassword('');
      setBusy(false);
    }
  };
  const watchCaps = (e: KeyboardEvent<HTMLInputElement>) => setCapsLock(e.getModifierState('CapsLock'));

  const goToProvider = async () => {
    setError('');
    setDismissed(true);
    setLeaving(true);
    try {
      await signInWithSso();  // on success the browser leaves this page for the provider
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The local API did not answer. Is it still running?');
      setLeaving(false);
    }
  };
  const showPasswords = !sso.passwordsOff || withPassword;

  return (
    <div className="relative flex h-full w-full flex-col overflow-y-auto bg-bg">
      <SignalGlow />

      <main className="relative flex flex-1 flex-col items-center justify-center px-5 py-14">
        <div className="nc-icon-in">
          <LogoSymbol size={76} title="NeuroCode" />
        </div>

        <div className="nc-arrive mt-7 flex w-full max-w-[340px] flex-col items-center [animation-delay:120ms]">
          <h1 className="text-center text-[27px] leading-[1.15] font-semibold tracking-[-0.028em] text-ink">
            Sign in to NeuroCode
          </h1>
          <p className="mt-2 text-center text-[15px] text-soft">
            {workspace ? `${workspace.name} workspace` : 'Your engineering workspace'}
          </p>

          {sso.enabled && (
            <div className="mt-8 w-full">
              <button
                type="button"
                onClick={() => void goToProvider()}
                disabled={leaving}
                className="flex h-[50px] w-full items-center justify-center gap-2 rounded-[14px] border border-line bg-surface/80 text-[15px] font-medium text-ink shadow-[0_22px_60px_-28px_var(--os-shadow)] backdrop-blur-xl transition-colors duration-200 hover:bg-surface-2 focus-visible:ring-4 focus-visible:ring-[rgb(255_159_28/0.16)] focus-visible:outline-none disabled:opacity-60"
              >
                {leaving ? <Loader2 className="size-4 animate-spin" /> : <KeyRound className="size-4" strokeWidth={2.2} />}
                Continue with {sso.label}
              </button>
              {showPasswords && (
                <div className="mt-6 flex items-center gap-3" aria-hidden>
                  <span className="h-px flex-1 bg-line" />
                  <span className="text-[12px] text-dim">or</span>
                  <span className="h-px flex-1 bg-line" />
                </div>
              )}
            </div>
          )}

          {!showPasswords ? (
            <div className="mt-6 text-center text-[13px] leading-relaxed text-dim">
              {shown && <p role="alert" className="mb-3 text-danger">{shown}</p>}
              <p>This workspace signs in through {sso.label}. Owners may use a password.</p>
              <button
                type="button"
                onClick={() => setWithPassword(true)}
                className="mt-1 text-ink underline decoration-line underline-offset-4 hover:decoration-ink"
              >
                Use a password
              </button>
            </div>
          ) : (
          <form onSubmit={submit} className={cn('w-full', sso.enabled ? 'mt-6' : 'mt-8')} noValidate>
            <div
              key={refusals}
              className={cn(
                'overflow-hidden rounded-[14px] border bg-surface/80 shadow-[0_22px_60px_-28px_var(--os-shadow)] backdrop-blur-xl transition-[border-color,box-shadow] duration-200',
                shown
                  ? 'border-danger/55'
                  : 'border-line focus-within:border-[rgb(255_159_28/0.6)] focus-within:ring-4 focus-within:ring-[rgb(255_159_28/0.16)]',
                refusals > 0 && 'nc-shake',
              )}
            >
              <input
                type="email"
                aria-label="Email"
                placeholder="Email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="username"
                autoFocus
                spellCheck={false}
                className="block h-[50px] w-full bg-transparent px-4 text-[15px] text-ink placeholder:text-dim focus:outline-none"
              />
              <div className="mx-4 h-px bg-line" aria-hidden />
              <div className="flex items-center">
                <input
                  type="password"
                  aria-label="Password"
                  placeholder="Password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyDown={watchCaps}
                  onKeyUp={watchCaps}
                  onBlur={() => setCapsLock(false)}
                  autoComplete="current-password"
                  className="block h-[50px] min-w-0 flex-1 bg-transparent pr-2 pl-4 text-[15px] text-ink placeholder:text-dim focus:outline-none"
                />
                <button
                  type="submit"
                  aria-label="Sign in"
                  disabled={!ready}
                  className={cn(
                    'mr-2.5 grid size-8 shrink-0 place-items-center rounded-full transition-[background-color,color,transform] duration-200 focus-visible:ring-2 focus-visible:ring-[rgb(255_159_28/0.6)] focus-visible:outline-none',
                    ready
                      ? 'bg-[#FF9F1C] text-[#1B1C20] hover:scale-[1.06] active:scale-95'
                      : 'cursor-default border border-line text-dim',
                  )}
                >
                  {busy ? <Loader2 className="size-4 animate-spin" /> : <ArrowRight className="size-4" strokeWidth={2.4} />}
                </button>
              </div>
            </div>

            <div className="mt-3 min-h-[20px] text-center text-[13px] leading-relaxed" aria-live="polite">
              {shown ? (
                <p role="alert" className="text-danger">{shown}</p>
              ) : capsLock ? (
                <p className="text-soft">Caps Lock is on.</p>
              ) : null}
            </div>
          </form>
          )}

          {showPasswords && (
            <p className="mt-6 max-w-[300px] text-center text-[13px] leading-relaxed text-dim">
              Forgot it? An Owner or Admin can set a new one.
            </p>
          )}
        </div>
      </main>

      <footer className="relative pb-7 text-center text-[12px] text-dim">
        NeuroCode {__APP_VERSION__} · in your own database
      </footer>
    </div>
  );
}
