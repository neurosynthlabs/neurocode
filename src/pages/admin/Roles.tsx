import { useMemo, useState, type SyntheticEvent } from 'react';
import { Lock, Plus, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Field, ListRow, Page, PageBody, PageHeader, Panel, SelectField, Tag } from '@/components/os';
import { api, type PermissionDef, type RoleDoc } from '@/lib/api';
import {
  VERBS, VERB_LABELS, cellOf, filedPermissions, moduleRows,
  type FiledPermission, type ModuleRow, type Verb,
} from '@/lib/access';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';
import { attempt, startingRole, useAdmin } from './load';
import { LoadError, Loading } from './kit';

/* ═══════════════════════════════════════════════════════════════
   ROLES & PERMISSIONS — a role read as a grid, not a wall. The
   rows are the modules and sub-modules the sidebar shows, in its
   own order; the columns are what a right lets someone do. The
   whole of a role fits on one screen, which is the point: a list
   of twenty-six checkboxes never told anybody what a role is.
   Every right is still reachable — two taps, inside its cell.
   ═══════════════════════════════════════════════════════════════ */

interface Access { roles: RoleDoc[]; catalogue: PermissionDef[] }

const loadAccess = async (): Promise<Access> => {
  const [roles, catalogue] = await Promise.all([api.admin.roles(), api.admin.permissions()]);
  return { roles, catalogue };
};
const sameSet = (a: string[], b: string[]) => a.length === b.length && a.every((x) => b.includes(x));
const people = (n: number) => `${n} ${n === 1 ? 'person' : 'people'}`;

/** What a cell says at a glance: all of them, some of them, none — or that there are none to hold. */
function chipLabel(held: number, total: number) {
  if (held === 0) return 'None';
  if (held === total) return 'All';
  return `${held} of ${total}`;
}

function Chip({ held, total, quiet }: { held: number; total: number; quiet?: boolean }) {
  const tone = held === 0 ? 'border-line/70 text-dim'
    : held === total ? 'border-brand/40 bg-brand/10 text-brand'
      : 'border-line-strong text-ink-2';
  return (
    <span className={cn('inline-flex h-7 min-w-[62px] items-center justify-center rounded-lg border px-2 text-[12.5px] tnum',
      tone, quiet && 'opacity-70')}>
      {chipLabel(held, total)}
    </span>
  );
}

/** One cell: the chip, and behind it the actual rights it stands for. */
function Cell({ items, held, editable, onToggle, compare, verb }: {
  items: FiledPermission[]; held: string[]; editable: boolean;
  onToggle: (id: string, on: boolean) => void;
  /** A second role's holdings, drawn under the first while Compare is on. */
  compare: { name: string; held: string[] } | null;
  verb: Verb;
}) {
  if (!items.length) {
    // Nothing of this kind exists here. An empty box would read as "none granted", which is a
    // different fact — so the cell says there is nothing to grant.
    return <span className="text-dim" aria-label="no rights of this kind">—</span>;
  }
  const mine = items.filter((p) => held.includes(p.id)).length;
  return (
    <div className="flex flex-col items-start gap-1">
      <Popover>
        <PopoverTrigger
          className="focus-brand flex min-h-[44px] items-center rounded-lg px-1 transition-colors hover:bg-surface-2/60"
          aria-label={`${VERB_LABELS[verb]}: ${chipLabel(mine, items.length)}`}
        >
          <Chip held={mine} total={items.length} />
        </PopoverTrigger>
        <PopoverContent className="w-[min(340px,calc(100vw-32px))] p-0">
          <div className="border-b border-line/60 px-4 py-2.5 text-[12.5px] font-medium text-ink-2">
            {VERB_LABELS[verb]} — {items.length === 1 ? '1 right' : `${items.length} rights`}
          </div>
          <div className="divide-y divide-line/50">
            {items.map((p) => (
              <label key={p.id} className={cn('flex items-start gap-3 px-4 py-2.5',
                editable ? 'cursor-pointer hover:bg-surface-2/50' : 'cursor-default')}>
                <input
                  type="checkbox" className="mt-0.5 size-4 shrink-0 accent-[var(--os-brand)]"
                  checked={held.includes(p.id)} disabled={!editable}
                  onChange={(e) => onToggle(p.id, e.target.checked)}
                />
                <span className="min-w-0 flex-1">
                  <span className="block text-[13.5px] font-medium text-ink">{p.label}</span>
                  <span className="block text-[12.5px] leading-snug text-soft">{p.description}</span>
                  <code className="mt-0.5 block font-mono text-[11.5px] text-dim">{p.id}</code>
                </span>
              </label>
            ))}
          </div>
        </PopoverContent>
      </Popover>
      {compare && (
        <span className="flex items-center gap-1.5 pl-1 text-[11.5px] text-dim">
          <Chip held={items.filter((p) => compare.held.includes(p.id)).length} total={items.length} quiet />
        </span>
      )}
    </div>
  );
}

/** The grid at 1024 and up: eleven rows of 44 px, the module name staying put as the eye moves right. */
function Matrix({ rows, permissions, held, editable, onToggle, compare }: {
  rows: ModuleRow[]; permissions: FiledPermission[]; held: string[]; editable: boolean;
  onToggle: (id: string, on: boolean) => void; compare: { name: string; held: string[] } | null;
}) {
  return (
    <table className="w-full border-separate border-spacing-0 text-left">
      <thead>
        <tr>
          <th className="sticky left-0 z-10 bg-surface pb-2 pr-3 text-[12.5px] font-semibold text-ink-2">Module</th>
          {VERBS.map((v) => (
            <th key={v} className="pb-2 pr-3 text-[12.5px] font-semibold text-ink-2">{VERB_LABELS[v]}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.key}>
            <th scope="row" className="sticky left-0 z-10 max-w-[220px] truncate border-t border-line/60 bg-surface py-2 pr-3 text-[13.5px] font-medium text-ink">
              {row.label}
            </th>
            {VERBS.map((v) => (
              <td key={v} className="border-t border-line/60 py-2 pr-3 align-middle">
                <Cell
                  items={cellOf(permissions, row, v)} held={held} editable={editable}
                  onToggle={onToggle} compare={compare} verb={v}
                />
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** The same eleven rows at 390 px: the name over its four chips, two by two. Nothing scrolls sideways. */
function MatrixStack({ rows, permissions, held, editable, onToggle, compare }: {
  rows: ModuleRow[]; permissions: FiledPermission[]; held: string[]; editable: boolean;
  onToggle: (id: string, on: boolean) => void; compare: { name: string; held: string[] } | null;
}) {
  return (
    <div className="divide-y divide-line/50 overflow-hidden rounded-xl border border-line/70">
      {rows.map((row) => (
        <div key={row.key} className="px-4 py-3">
          <div className="text-[13.5px] font-medium text-ink">{row.label}</div>
          <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-2">
            {VERBS.map((v) => (
              <div key={v} className="flex items-center gap-2">
                <span className="w-[52px] shrink-0 text-[12px] text-dim">{VERB_LABELS[v]}</span>
                <Cell
                  items={cellOf(permissions, row, v)} held={held} editable={editable}
                  onToggle={onToggle} compare={compare} verb={v}
                />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function Roles() {
  const { can } = useAuth();
  const { data, setData, error, reload } = useAdmin<Access>(loadAccess);
  const manage = can('roles:manage');
  const [sel, setSel] = useState<string | null>(null);
  const [against, setAgainst] = useState('');
  const [draft, setDraft] = useState<string[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);

  const permissions = useMemo(() => filedPermissions(data?.catalogue ?? []), [data?.catalogue]);
  const rows = useMemo(() => moduleRows(permissions), [permissions]);

  const role = data?.roles.find((r) => r.id === sel) ?? data?.roles[0];
  const perms = draft ?? role?.permissions ?? [];
  const editable = !!(manage && role && !role.builtin);
  const dirty = !!(draft && role && !sameSet(draft, role.permissions));
  const other = data?.roles.find((r) => r.id === against && r.id !== role?.id);
  const compare = other ? { name: other.name, held: other.permissions } : null;

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
    setSel(null);  // back to the first role in the list
    setDraft(null);
    toast.success(`${r.name} deleted`);
  };

  return (
    <Page>
      <PageHeader
        title="Roles & permissions"
        subtitle="A role is a named set of permissions, filed where the sidebar files them: a module, what it lets someone do, and the rights behind each cell. A person’s access is everything their roles allow."
        actions={<Button size="sm" onClick={() => setCreating(true)} disabled={!manage}><Plus className="size-3.5" />New role</Button>}
      />
      <PageBody>
        {error ? <LoadError error={error} onRetry={reload} /> : !data || !role ? <Loading /> : (
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-[290px_minmax(0,1fr)]">
            <Panel flush title="Roles" eyebrow={`${data.roles.length} in this workspace · ${data.roles.filter((r) => r.builtin).length} built in`} className="hidden self-start lg:block">
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
              {/* At 390 the list of roles is a field, not a column: a 290 px panel above eleven rows is
                  the wall this screen was redrawn to get rid of. */}
              <SelectField
                className="mb-4 lg:hidden" label="Role"
                value={role.id} onChange={pick}
                options={data.roles.map((r) => ({ value: r.id, label: r.builtin ? `${r.name} (built in)` : r.name }))}
              />
              <p className="text-[13.5px] text-soft">{role.description || 'No description.'}</p>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-[12.5px] text-dim">
                <Tag tone={role.builtin ? 'neutral' : 'brand'}>{role.builtin ? 'Built-in' : 'Custom'}</Tag>
                <span className="tnum">{perms.length} of {data.catalogue.length} permissions · held by {people(role.members)}</span>
              </div>
              {role.builtin && (
                <p className="mt-3 text-[12.5px] leading-relaxed text-dim">
                  Built-in roles never drift, so everyone can rely on what they mean. To grant a different set, create a custom role.
                </p>
              )}

              <div className="mt-4 flex flex-wrap items-end justify-between gap-3">
                <p className="text-[12.5px] text-dim">
                  A cell stands for the rights of that kind in that part of the product. Open one to see them by name.
                </p>
                <SelectField
                  className="w-full sm:w-[220px]" label="Compare with"
                  value={against} onChange={setAgainst}
                  options={[{ value: '', label: 'Nothing: this role alone' },
                            ...data.roles.filter((r) => r.id !== role.id).map((r) => ({ value: r.id, label: r.name }))]}
                />
              </div>
              {compare && (
                <p className="mt-2 text-[12.5px] text-dim">
                  The quieter chip under each one is <span className="text-ink-2">{compare.name}</span>.
                </p>
              )}

              <div className="mt-4 hidden lg:block">
                <Matrix rows={rows} permissions={permissions} held={perms} editable={editable}
                        onToggle={toggle} compare={compare} />
              </div>
              <div className="mt-4 lg:hidden">
                <MatrixStack rows={rows} permissions={permissions} held={perms} editable={editable}
                             onToggle={toggle} compare={compare} />
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
  const [from, setFrom] = useState(() => startingRole(roles));
  const [busy, setBusy] = useState(false);

  const close = () => { onOpenChange(false); setName(''); setDescription(''); setFrom(startingRole(roles)); };
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
