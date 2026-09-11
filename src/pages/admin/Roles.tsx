import { useState, type SyntheticEvent } from 'react';
import { Lock, Plus, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Field, ListRow, Page, PageBody, PageHeader, Panel, SelectField, Tag } from '@/components/os';
import { api, type PermissionDef, type RoleDoc } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { permissions as CATALOGUE } from '@/mock/rbac';
import { cn } from '@/lib/utils';
import { attempt, demoRoles, useAdmin } from './load';
import { DemoNote, LoadError, Loading } from './kit';

interface Access { roles: RoleDoc[]; catalogue: PermissionDef[] }

const loadAccess = async (): Promise<Access> => {
  const [roles, catalogue] = await Promise.all([api.admin.roles(), api.admin.permissions()]);
  return { roles, catalogue };
};
const DEMO: Access = { roles: demoRoles, catalogue: CATALOGUE };
const GROUPS = ['Work', 'Gates', 'Knowledge', 'Platform', 'Admin'];
const sameSet = (a: string[], b: string[]) => a.length === b.length && a.every((x) => b.includes(x));
const people = (n: number) => `${n} ${n === 1 ? 'person' : 'people'}`;

export default function Roles() {
  const { can } = useAuth();
  const { data, setData, error, live, reload } = useAdmin(loadAccess, DEMO);
  const manage = live && can('roles:manage');
  const [sel, setSel] = useState<string | null>(null);
  const [draft, setDraft] = useState<string[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);

  const role = data?.roles.find((r) => r.id === sel) ?? data?.roles[0];
  const perms = draft ?? role?.permissions ?? [];
  const editable = !!(manage && role && !role.builtin);
  const dirty = !!(draft && role && !sameSet(draft, role.permissions));

  const pick = (id: string) => { setSel(id); setDraft(null); };
  const toggle = (p: string, on: boolean) => setDraft(on ? [...perms, p] : perms.filter((x) => x !== p));
  const replace = (r: RoleDoc) => setData((d) => d && {
    ...d, roles: d.roles.some((x) => x.id === r.id) ? d.roles.map((x) => (x.id === r.id ? r : x)) : [...d.roles, r],
  });

  const save = async () => {
    if (!role || !dirty || busy) return;
    setBusy(true);
    const r = await attempt(() => api.admin.updateRole(role.id, { permissions: perms }), 'Role not saved');
    setBusy(false);
    if (!r) return;
    replace(r);
    setDraft(null);
    toast.success(`${r.name} saved`, { description: `It applies at once to the ${people(r.members)} who hold it.` });
  };
  const remove = async () => {
    if (!role || busy) return;
    setBusy(true);
    const r = await attempt(() => api.admin.deleteRole(role.id), 'Role not deleted');
    setBusy(false);
    if (!r) return;
    setData((d) => d && { ...d, roles: d.roles.filter((x) => x.id !== r.id) });
    pick('owner');
    toast.success(`${r.name} deleted`);
  };

  return (
    <Page>
      <PageHeader
        title="Roles & permissions"
        subtitle="A role is a named set of permissions, and a person’s access is everything their roles allow. The five built-in roles are fixed; add custom ones beside them."
        actions={<Button size="sm" onClick={() => setCreating(true)} disabled={!manage}><Plus className="size-3.5" />New role</Button>}
      />
      <PageBody>
        {!live && <DemoNote what="Custom roles" />}
        {error ? <LoadError error={error} onRetry={reload} /> : !data || !role ? <Loading /> : (
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-[290px_minmax(0,1fr)]">
            <Panel flush title="Roles" eyebrow={`${data.roles.length} in this workspace`} className="self-start">
              <div className="py-1">
                {data.roles.map((r) => (
                  <ListRow key={r.id} active={r.id === role.id} onClick={() => pick(r.id)}>
                    <div className="flex items-center gap-2">
                      <span className="min-w-0 flex-1 truncate text-[14px] font-medium text-ink">{r.name}</span>
                      {r.builtin && <Lock className="size-3.5 shrink-0 text-dim" aria-label="Built-in" />}
                    </div>
                    <div className="mt-0.5 text-[12.5px] text-dim">{r.permissions.length} permissions · {people(r.members)}</div>
                  </ListRow>
                ))}
              </div>
            </Panel>

            <Panel
              eyebrow={role.builtin ? 'Built-in role · fixed' : 'Custom role'}
              title={role.name}
              actions={editable && (
                <div className="flex gap-2">
                  {dirty && <Button size="sm" variant="ghost" onClick={() => setDraft(null)}>Discard</Button>}
                  <Button size="sm" onClick={save} disabled={!dirty || busy}>Save changes</Button>
                </div>
              )}
            >
              <p className="text-[13.5px] text-soft">{role.description || 'No description.'}</p>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-[12.5px] text-dim">
                <Tag tone={role.builtin ? 'neutral' : 'brand'}>{role.builtin ? 'Built-in' : 'Custom'}</Tag>
                <span>{perms.length} of {data.catalogue.length} permissions · held by {people(role.members)}</span>
              </div>
              {role.builtin && (
                <p className="mt-3 text-[12.5px] leading-relaxed text-dim">
                  Built-in roles never drift, so everyone can rely on what they mean. To grant a different set, create a custom role.
                </p>
              )}

              <div className="mt-5 space-y-5">
                {GROUPS.map((g) => {
                  const items = data.catalogue.filter((p) => p.group === g);
                  if (!items.length) return null;
                  return (
                    <div key={g}>
                      <div className="mb-1.5 text-[12.5px] font-semibold text-ink-2">{g}</div>
                      <div className="divide-y divide-line/50 overflow-hidden rounded-xl border border-line/70">
                        {items.map((p) => (
                          <label key={p.id} className={cn('flex items-start gap-3 px-4 py-2.5', editable ? 'cursor-pointer hover:bg-surface-2/50' : 'cursor-default')}>
                            <input
                              type="checkbox" className="mt-0.5 size-4 shrink-0 accent-[var(--os-brand)]"
                              checked={perms.includes(p.id)} disabled={!editable} onChange={(e) => toggle(p.id, e.target.checked)}
                            />
                            <span className="min-w-0 flex-1">
                              <span className="block text-[13.5px] font-medium text-ink">{p.label}</span>
                              <span className="block text-[12.5px] leading-snug text-soft">{p.description}</span>
                            </span>
                            <code className="hidden shrink-0 font-mono text-[11.5px] text-dim sm:block">{p.id}</code>
                          </label>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>

              {editable && (
                <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-line/60 pt-4">
                  <p className="text-[12.5px] text-dim">
                    {role.members ? `Move its ${people(role.members)} to another role before deleting it.` : 'Nobody holds this role.'}
                  </p>
                  <Button size="sm" variant="destructive" onClick={remove} disabled={busy || role.members > 0}>
                    <Trash2 className="size-3.5" />Delete role
                  </Button>
                </div>
              )}
            </Panel>
          </div>
        )}
      </PageBody>
      {data && (
        <NewRole open={creating} onOpenChange={setCreating} roles={data.roles} onCreated={(r) => { replace(r); pick(r.id); }} />
      )}
    </Page>
  );
}

function NewRole({ open, onOpenChange, roles, onCreated }: {
  open: boolean; onOpenChange: (open: boolean) => void; roles: RoleDoc[]; onCreated: (r: RoleDoc) => void;
}) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [from, setFrom] = useState('engineer');
  const [busy, setBusy] = useState(false);

  const close = () => { onOpenChange(false); setName(''); setDescription(''); setFrom('engineer'); };
  const submit = async (e: SyntheticEvent) => {
    e.preventDefault();
    if (!name.trim() || busy) return;
    setBusy(true);
    const base = roles.find((r) => r.id === from)?.permissions ?? [];
    const r = await attempt(
      () => api.admin.createRole({ name: name.trim(), description: description.trim(), permissions: base }), 'Role not created',
    );
    setBusy(false);
    if (!r) return;
    onCreated(r);
    close();
    toast.success(`${r.name} created`, { description: 'Adjust its permissions, then give it to people in People.' });
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) close(); }}>
      <DialogContent className="sm:max-w-[440px]">
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>New role</DialogTitle>
            <DialogDescription>Start from an existing role’s permissions, then adjust them.</DialogDescription>
          </DialogHeader>
          <Field label="Name" value={name} onChange={setName} placeholder="Release manager" autoFocus />
          <Field label="What it is for" value={description} onChange={setDescription} placeholder="Signs deploys, nothing else" />
          <SelectField
            label="Start from" value={from} onChange={setFrom}
            options={[{ value: '', label: 'Nothing: no permissions' }, ...roles.map((r) => ({ value: r.id, label: r.name }))]}
          />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={close}>Cancel</Button>
            <Button type="submit" disabled={!name.trim() || busy}>{busy ? 'Creating…' : 'Create role'}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
