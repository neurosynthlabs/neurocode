import { useCallback, useEffect, useState, type SyntheticEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { Field, KV, More, Page, PageBody, PageHeader, Panel, Stat, StatGrid, Tag } from '@/components/os';
import { api, type SandboxInfo, type SsoConfig, type WorkspaceInfo } from '@/lib/api';
import { useAccess } from '@/lib/access';
import { useAuth } from '@/lib/auth';
import { useData } from '@/lib/data';
import { attempt, useAdmin, when } from './load';
import { LoadError, Loading } from './kit';
import { plural } from '@/lib/words';

const loadWorkspace = () => api.admin.workspace();
/** "30 s", "15 min", "1 h": the lock-out as someone reads it. */
const pause = (s: number) => (s < 120 ? `${s} s` : s < 7200 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`);

export default function WorkspacePage() {
  const nav = useNavigate();
  const { can, refresh } = useAuth();
  const { health, reset } = useData();
  // The workspace's own roles, custom ones included, so a claim map names what this workspace has.
  const { catalogue } = useAccess();
  const roles = catalogue?.roles.map((r) => ({ id: r.id, name: r.name })) ?? [];
  const { data: ws, setData, error, reload } = useAdmin<WorkspaceInfo>(loadWorkspace);
  const manage = can('workspace:admin');
  const [name, setName] = useState<string | null>(null);
  // Resetting is two clicks: the first arms the button for four seconds, the second does it.
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const id = window.setTimeout(() => setArmed(false), 4000);
    return () => window.clearTimeout(id);
  }, [armed]);

  const renamed = !!ws && !!name?.trim() && name.trim() !== ws.name;
  const rename = async (e: SyntheticEvent) => {
    e.preventDefault();
    if (!renamed || !name) return;
    const next = await attempt(() => api.admin.updateWorkspace(name.trim()), 'Workspace not renamed');
    if (!next) return;
    setData(next);
    setName(null);
    void refresh();  // the account menu shows the name too
    toast.success(`Renamed to ${next.name}`);
  };
  const resetData = async () => {
    if (!armed) { setArmed(true); return; }
    setArmed(false);
    const done = await reset();
    if (!done) return;
    toast.success('Workspace emptied', {
      description: [
        done.backup ? `A backup was taken first: ${done.backup}.` : 'No backup was taken: pg_dump is not on this machine.',
        `People, roles, teams, keys, the audit log and ${plural(done.counts.agents ?? 0, 'agent')} were kept.`,
      ].join(' '),
    });
  };

  return (
    <Page>
      <PageHeader title="Workspace" subtitle="Name, security, sign-on and sandbox, shared by everyone here." />
      <PageBody>
        {error ? <LoadError error={error} onRetry={reload} /> : !ws ? <Loading /> : (
          <div className="space-y-5">
            <StatGrid cols={3}>
              <Stat label="People" value={ws.people} sub="with an account" onClick={() => nav('/admin/users')} />
              <Stat label="Roles" value={ws.roles} sub={`${ws.builtinRoles} built in`} onClick={() => nav('/admin/roles')} />
              <Stat label="Teams" value={ws.teams} sub="groups of people" onClick={() => nav('/admin/teams')} />
            </StatGrid>

            <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
              <Panel title="Name" eyebrow={`Created ${when(ws.createdAt)}`}>
                <form onSubmit={rename} className="flex flex-col gap-2 sm:flex-row sm:items-end">
                  <Field className="flex-1" label="Workspace name" value={name ?? ws.name} onChange={setName} disabled={!manage} />
                  <Button type="submit" disabled={!manage || !renamed}>Rename</Button>
                </form>
                <p className="mt-2 text-[12.5px] text-dim">Shown at sign-in and in every account menu.</p>
              </Panel>
              <Panel title="Security">
                <KV k="Sessions" v={`${plural(ws.security.sessionDays, 'day')}, in an HttpOnly cookie`} />
                <KV k="Passwords" v={`scrypt, at least ${plural(ws.security.minPassword, 'character')}`} />
                <KV k="Wrong passwords" v={`${ws.security.loginAttempts} in a row pause that email for ${pause(ws.security.lockoutSeconds)}`} />
                <KV k="Model keys" v="kept by the API, never shown in full" />
                <KV k="On the record" v="sign-ins, access, keys, resets" />
              </Panel>
            </div>

            {/* Both read routes need workspace:admin, so there is nothing here for anybody else to see. */}
            {manage && (
              <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
                <SingleSignOn roles={roles} />
                <Sandbox />
              </div>
            )}

            <Panel title="Work data" eyebrow="PostgreSQL, on this machine" className="border-danger/30"
              about={<><p>Reset empties projects, tasks, plans, runs, memory, approvals and the activity log. A backup is taken first.</p>
                <p>People, roles, teams, the agent roster, keys and the audit log are kept.</p></>}>
              {health && <KV k="Database" v={health.db} mono />}
              {health && (
                // The planner's estimates, as /health reports them: a table it has not analysed yet reads 0.
                <KV k="Holds" v={[
                  plural(health.counts.tasks ?? 0, 'task'), plural(health.counts.plans ?? 0, 'plan'),
                  plural(health.counts.memory_facts ?? 0, 'fact'), plural(health.counts.activity ?? 0, 'log line'),
                ].join(' · ')} />
              )}
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                <p className="max-w-xl text-[12.5px] leading-relaxed text-soft">
                  Empties all work, after a backup.
                </p>
                <Button size="sm" variant="destructive" onClick={() => void resetData()} disabled={!manage}>
                  {armed ? 'Click again to confirm' : 'Reset work data'}
                </Button>
              </div>
            </Panel>
          </div>
        )}
      </PageBody>
    </Page>
  );
}

/* ── The fence around the runtime's commands ─────────────────────
   Read, never described: what this panel shows is what the server measured on the machine it is
   running on, including "there is no sandbox here" and why. The one decision a person makes is the
   network, because a test suite that installs its packages needs it and nobody should find that out
   by reading code. */
function Sandbox() {
  const [box, setBox] = useState<SandboxInfo | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    api.admin.sandbox().then(
      (found) => { if (live) setBox(found); },
      (e: unknown) => console.error('[NeuroCode] GET /admin/sandbox failed:', e),
    );
    return () => { live = false; };
  }, []);

  const save = async (patch: { enabled: boolean; network: boolean }, said: string) => {
    setBusy(true);
    const next = await attempt(() => api.admin.updateSandbox(patch), 'The sandbox was not changed');
    setBusy(false);
    if (!next) return;
    setBox(next);
    toast.success(said);
  };

  if (!box) return null;
  const fenced = box.inForce.confinesWrites;
  return (
    <Panel
      title="Sandbox"
      eyebrow="Around run commands"
      actions={<Tag tone={fenced ? 'ok' : box.inForce.kind === 'none' ? 'warn' : 'info'}>{box.inForce.name}</Tag>}
    >
      <p className="mb-3 text-[12.5px] leading-relaxed text-soft">{box.inForce.words}</p>
      <KV k="This machine offers" v={box.detected.name} wrap />
      {box.detected.why && <p className="mt-1 text-[12px] leading-relaxed text-warn">{box.detected.why}</p>}
      <div className="mt-3 flex items-center justify-between gap-4 border-t border-line/70 pt-3">
        <div>
          <p className="text-[13px] font-medium text-ink">Let a command reach the network</p>
          <p className="mt-0.5 max-w-md text-[12px] leading-relaxed text-dim">
            Off blocks every send. Turn on if tests install packages.
          </p>
        </div>
        <Switch
          checked={box.network}
          disabled={busy}
          aria-label="Let a sandboxed command reach the network"
          onCheckedChange={(on) => void save({ enabled: box.enabled, network: on },
            on ? 'Sandboxed commands may reach the network' : 'The network is off inside the sandbox')}
        />
      </div>
      <div className="mt-3 flex items-center justify-between gap-4 border-t border-line/70 pt-3">
        <div>
          <p className="text-[13px] font-medium text-ink">Sandbox the runtime's commands</p>
          <p className="mt-0.5 max-w-md text-[12px] leading-relaxed text-dim">
            {box.serverAllows
              ? 'Off runs tests and checks with everything this account can reach.'
              : 'Started with NEUROCODE_SANDBOX=false; a screen cannot undo it.'}
          </p>
        </div>
        <Switch
          checked={box.enabled}
          disabled={busy || !box.serverAllows}
          aria-label="Sandbox the commands a run executes"
          onCheckedChange={(on) => void save({ enabled: on, network: box.network },
            on ? 'Commands run behind the sandbox' : 'Commands run unsandboxed')}
        />
      </div>
    </Panel>
  );
}

/* ── Single sign-on ──────────────────────────────────────────────
   An issuer, a client id, a secret kept the way a model key is kept, and a map from a claim to a role
   here. The Owner role is not offerable: the API refuses a map that names it, and a screen that let
   somebody try would be a screen that teaches the wrong thing. */
function SingleSignOn({ roles }: { roles: { id: string; name: string }[] }) {
  const [doc, setDoc] = useState<SsoConfig | null>(null);
  const [draft, setDraft] = useState<Partial<SsoConfig> & { clientSecret?: string }>({});
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    api.admin.sso().then(
      (found) => { setDoc(found); setDraft({}); },
      (e: unknown) => console.error('[NeuroCode] GET /admin/sso failed:', e),
    );
  }, []);
  useEffect(load, [load]);

  if (!doc) return null;
  const at = <K extends keyof SsoConfig>(k: K): SsoConfig[K] => (draft[k] ?? doc[k]) as SsoConfig[K];
  const edited = Object.keys(draft).length > 0;
  const suggested = `${window.location.origin}${doc.callbackPath}`;

  const save = async (extra: Record<string, unknown> = {}) => {
    setBusy(true);
    const next = await attempt(() => api.admin.updateSso({ ...draft, ...extra }), 'Single sign-on was not saved');
    setBusy(false);
    if (!next) return;
    setDoc(next);
    setDraft({});
    toast.success('Single sign-on saved');
  };

  const mapped = Object.entries(at('roleMap'));
  const addClaim = (claim: string, role: string) => {
    if (!claim.trim()) return;
    setDraft((d) => ({ ...d, roleMap: { ...at('roleMap'), [claim.trim()]: role } }));
  };
  const dropClaim = (claim: string) => {
    const left = { ...at('roleMap') };
    delete left[claim];
    setDraft((d) => ({ ...d, roleMap: left }));
  };

  return (
    <Panel
      title="Single sign-on"
      eyebrow="OpenID Connect"
      about="Google, Okta, Entra, or any provider with a discovery document."
      actions={<Tag tone={doc.ready ? 'ok' : 'neutral'}>{doc.ready ? 'On' : 'Off'}</Tag>}
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Issuer" value={at('issuer')} onChange={(v) => setDraft((d) => ({ ...d, issuer: v }))}
          mono placeholder="https://accounts.google.com"
          hint="Only its .well-known/openid-configuration is read." />
        <Field label="Client id" value={at('clientId')} onChange={(v) => setDraft((d) => ({ ...d, clientId: v }))}
          mono />
        <Field label="Client secret" type="password" value={draft.clientSecret ?? ''} mono autoComplete="off"
          onChange={(v) => setDraft((d) => ({ ...d, clientSecret: v }))}
          hint={doc.hasSecret ? `Set · ${doc.secretMask}. Type a new one to replace it.`
                              : 'Kept beside the model keys, never shown again in full.'} />
        <Field label="Button label" value={at('label')} onChange={(v) => setDraft((d) => ({ ...d, label: v }))}
          hint="The sign-in screen says “Continue with …”." />
        <Field className="sm:col-span-2" label="Redirect address" value={at('redirectUri')} mono
          onChange={(v) => setDraft((d) => ({ ...d, redirectUri: v }))}
          hint={<>Register at the provider. For this browser: <code className="font-mono [overflow-wrap:anywhere]">{suggested}</code></>} />
      </div>

      <More className="mt-4 border-t border-line/70 pt-3" label={mapped.length ? `Roles from claims · ${mapped.length}` : 'Roles from claims'}>
        <div className="flex flex-wrap items-end gap-3">
          <Field className="w-44" label="Role claim" value={at('roleClaim')} mono
            onChange={(v) => setDraft((d) => ({ ...d, roleClaim: v }))}
            hint="Usually groups or roles." />
          <ClaimAdder roles={roles} onAdd={addClaim} />
        </div>
        {mapped.length > 0 && (
          <ul className="mt-3 flex flex-wrap gap-2">
            {mapped.map(([claim, role]) => (
              <li key={claim} className="flex items-center gap-2 rounded-lg border border-line bg-surface-2/60 px-2.5 py-1 text-[12.5px]">
                <span className="font-mono text-ink">{claim}</span>
                <span className="text-dim">→</span>
                <span className="text-ink">{roles.find((r) => r.id === role)?.name ?? role}</span>
                <button type="button" onClick={() => dropClaim(claim)} className="text-dim hover:text-danger" aria-label={`Remove ${claim}`}>×</button>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 text-[12px] leading-relaxed text-dim">
          Applied at every sign-in. No claim touches an Owner or grants the Owner role.
        </p>
      </More>

      <div className="mt-4 space-y-3 border-t border-line/70 pt-3">
        <Choice label="Turn it on" hint="The sign-in screen then offers the provider’s button."
          on={at('enabled')} disabled={busy}
          onChange={(v) => setDraft((d) => ({ ...d, enabled: v }))} />
        <Choice label="Make accounts for new people"
          hint="Off: someone new to this workspace is told to ask an admin."
          on={at('createUsers')} disabled={busy}
          onChange={(v) => setDraft((d) => ({ ...d, createUsers: v }))} />
        <Choice label="Require it" hint="Only Owners keep passwords, so a broken provider cannot lock everyone out."
          on={at('requireSso')} disabled={busy || !doc.ready}
          onChange={(v) => setDraft((d) => ({ ...d, requireSso: v }))} />
      </div>

      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={load} disabled={!edited || busy}>Discard</Button>
        <Button size="sm" onClick={() => void save()} disabled={!edited || busy}>Save</Button>
      </div>
    </Panel>
  );
}

function Choice({ label, hint, on, disabled, onChange }: {
  label: string; hint: string; on: boolean; disabled: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div>
        <p className="text-[13px] font-medium text-ink">{label}</p>
        <p className="mt-0.5 max-w-md text-[12px] leading-relaxed text-dim">{hint}</p>
      </div>
      <Switch checked={on} disabled={disabled} aria-label={label} onCheckedChange={onChange} />
    </div>
  );
}

function ClaimAdder({ roles, onAdd }: {
  roles: { id: string; name: string }[]; onAdd: (claim: string, role: string) => void;
}) {
  // The Owner role is absent on purpose: the API refuses a map that names it, and offering it here
  // would be offering something that cannot happen.
  const offered = roles.filter((r) => r.id !== 'owner');
  const [claim, setClaim] = useState('');
  const [role, setRole] = useState(offered[0]?.id ?? 'viewer');
  return (
    <div className="flex flex-wrap items-end gap-2">
      <Field className="w-44" label="Claim value" value={claim} onChange={setClaim} mono
        placeholder="platform-engineers" />
      <label className="block">
        <span className="mb-1.5 block text-[12.5px] font-medium text-soft">Carries</span>
        <select value={role} onChange={(e) => setRole(e.target.value)}
          className="h-9 rounded-lg border border-line bg-surface-2/60 px-2.5 text-[13.5px] text-ink-2 hover:text-ink focus-visible:border-brand">
          {offered.map((r) => <option key={r.id} value={r.id} className="bg-surface">{r.name}</option>)}
        </select>
      </label>
      <Button type="button" size="sm" variant="secondary" disabled={!claim.trim()}
        onClick={() => { onAdd(claim, role); setClaim(''); }}>Add</Button>
    </div>
  );
}
