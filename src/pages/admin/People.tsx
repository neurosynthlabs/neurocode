import { useState, type SyntheticEvent } from 'react';
import { KeyRound, Plus, UserRoundCheck, UserRoundX } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Cell, DataTable, Field, Page, PageBody, PageHeader, Panel, Row, StatusText, Tag } from '@/components/os';
import { api, type Person, type RoleDoc, type TeamDoc } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { demoTeams, demoUsers } from '@/mock/rbac';
import { attempt, demoRoles, temporaryPassword, useAdmin, when } from './load';
import { Avatar, Credentials, DemoNote, LoadError, Loading, RolePicker } from './kit';

interface Directory { people: Person[]; roles: RoleDoc[]; teams: TeamDoc[] }

// Someone who manages only teams can open this screen too; the lists they may not read stay empty.
const loadDirectory = async (): Promise<Directory> => {
  const [people, roles, teams] = await Promise.all([
    api.admin.users(), api.admin.roles().catch(() => []), api.admin.teams().catch(() => []),
  ]);
  return { people, roles, teams };
};
const DEMO: Directory = { people: demoUsers, roles: demoRoles, teams: demoTeams };
const sameSet = (a: string[], b: string[]) => a.length === b.length && a.every((x) => b.includes(x));

export default function People() {
  const { can, user: me } = useAuth();
  const { data, setData, error, live, reload } = useAdmin(loadDirectory, DEMO);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Person | null>(null);
  const manage = live && can('users:manage');
  const isOwner = !!me?.roles.includes('owner');

  const upsert = (p: Person) => {
    setData((d) => d && {
      ...d, people: d.people.some((x) => x.id === p.id) ? d.people.map((x) => (x.id === p.id ? p : x)) : [...d.people, p],
    });
    reload();  // role member counts move too
  };
  const roleLabel = (id: string) => data?.roles.find((r) => r.id === id)?.name ?? id;
  const teamLabel = (id: string) => data?.teams.find((t) => t.id === id)?.name ?? id;
  const active = data?.people.filter((p) => p.status === 'active').length ?? 0;

  return (
    <Page>
      <PageHeader
        title="People"
        subtitle="Everyone signs in with their own account, and their roles decide what they can change. Every change here lands in the audit log."
        actions={<Button size="sm" onClick={() => setAdding(true)} disabled={!manage}><Plus className="size-3.5" />Add person</Button>}
      />
      <PageBody>
        {!live && <DemoNote what="Accounts, roles and passwords" />}
        {error ? <LoadError error={error} onRetry={reload} /> : !data ? <Loading /> : (
          <div className="space-y-5">
            <Panel flush title={`${data.people.length} ${data.people.length === 1 ? 'person' : 'people'}`} eyebrow={`${active} can sign in`}>
              <DataTable head={['Person', 'Roles', 'Teams', 'Status', 'Last sign-in', '']}>
                {data.people.map((p) => (
                  <Row key={p.id} onClick={manage ? () => setEditing(p) : undefined}>
                    <Cell>
                      <span className="flex items-center gap-3">
                        <Avatar name={p.name} />
                        <span className="min-w-0">
                          <span className="block truncate font-medium text-ink">
                            {p.name}{p.id === me?.id && <span className="ml-1.5 text-[12px] font-normal text-dim">you</span>}
                          </span>
                          <span className="block truncate text-[12.5px] text-dim">{p.email}</span>
                        </span>
                      </span>
                    </Cell>
                    <Cell>
                      <span className="flex flex-wrap gap-1">
                        {p.roles.length === 0 && <span className="text-[12.5px] text-dim">none</span>}
                        {p.roles.map((r) => <Tag key={r} tone={r === 'owner' || r === 'admin' ? 'brand' : 'neutral'}>{roleLabel(r)}</Tag>)}
                      </span>
                    </Cell>
                    <Cell className="text-[13px] whitespace-nowrap text-soft">{p.teams.map(teamLabel).join(', ') || '—'}</Cell>
                    <Cell><StatusText state={p.status} /></Cell>
                    <Cell className="text-[13px] whitespace-nowrap text-soft">{when(p.lastLoginAt)}</Cell>
                    <Cell className="text-right">
                      {manage && <Button size="xs" variant="ghost" onClick={(e) => { e.stopPropagation(); setEditing(p); }}>Edit</Button>}
                    </Cell>
                  </Row>
                ))}
              </DataTable>
            </Panel>

            <Panel flush title="What each role can do" eyebrow="Roles & permissions has the details">
              <div className="divide-y divide-line/60">
                {data.roles.map((r) => (
                  <div key={r.id} className="flex flex-col gap-0.5 px-5 py-3 sm:flex-row sm:items-baseline sm:gap-4">
                    <span className="shrink-0 text-[13.5px] font-medium text-ink sm:w-44">{r.name}</span>
                    <span className="min-w-0 flex-1 text-[13px] text-soft">{r.description}</span>
                    <span className="tnum shrink-0 text-[12.5px] text-dim">{r.members} {r.members === 1 ? 'person' : 'people'}</span>
                  </div>
                ))}
              </div>
            </Panel>
          </div>
        )}
      </PageBody>

      {data && <AddPerson open={adding} onOpenChange={setAdding} roles={data.roles} isOwner={isOwner} onAdded={upsert} />}
      {data && editing && (
        <EditPerson
          person={editing} roles={data.roles} isOwner={isOwner} isMe={editing.id === me?.id}
          onClose={() => setEditing(null)} onSaved={upsert}
        />
      )}
    </Page>
  );
}

function AddPerson({ open, onOpenChange, roles, isOwner, onAdded }: {
  open: boolean; onOpenChange: (open: boolean) => void; roles: RoleDoc[]; isOwner: boolean; onAdded: (p: Person) => void;
}) {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState(temporaryPassword);
  const [picked, setPicked] = useState<string[]>(['engineer']);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<Person | null>(null);
  const ready = !!name.trim() && !!email.trim() && password.length >= 10 && picked.length > 0;

  const close = () => {
    onOpenChange(false);
    setName(''); setEmail(''); setPassword(temporaryPassword()); setPicked(['engineer']); setCreated(null);
  };
  const submit = async (e: SyntheticEvent) => {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    const p = await attempt(() => api.admin.createUser({ name: name.trim(), email: email.trim(), password, roles: picked }), 'Person not added');
    setBusy(false);
    if (p) { setCreated(p); onAdded(p); }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) close(); }}>
      <DialogContent className="sm:max-w-[520px]">
        {created ? (
          <>
            <DialogHeader>
              <DialogTitle>{created.name} can sign in now</DialogTitle>
              <DialogDescription>Hand these over privately. They can choose their own password from the account menu.</DialogDescription>
            </DialogHeader>
            <Credentials email={created.email} password={password} />
            <DialogFooter><Button onClick={close}>Done</Button></DialogFooter>
          </>
        ) : (
          <form onSubmit={submit} className="grid gap-4">
            <DialogHeader>
              <DialogTitle>Add a person</DialogTitle>
              <DialogDescription>They sign in with this email and a temporary password.</DialogDescription>
            </DialogHeader>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Name" value={name} onChange={setName} autoFocus autoComplete="off" />
              <Field label="Email" type="email" value={email} onChange={setEmail} autoComplete="off" placeholder="name@company.com" />
            </div>
            <div className="flex items-end gap-2">
              <Field className="flex-1" label="Temporary password" value={password} onChange={setPassword} mono autoComplete="off" />
              <Button type="button" variant="outline" onClick={() => setPassword(temporaryPassword())}>New one</Button>
            </div>
            <div>
              <div className="mb-1.5 text-[12.5px] font-medium text-soft">Roles</div>
              <RolePicker roles={roles} value={picked} onChange={setPicked} isOwner={isOwner} />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={close}>Cancel</Button>
              <Button type="submit" disabled={!ready || busy}>{busy ? 'Adding…' : 'Add person'}</Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

function EditPerson({ person, roles, isOwner, isMe, onClose, onSaved }: {
  person: Person; roles: RoleDoc[]; isOwner: boolean; isMe: boolean; onClose: () => void; onSaved: (p: Person) => void;
}) {
  const [name, setName] = useState(person.name);
  const [picked, setPicked] = useState(person.roles);
  const [busy, setBusy] = useState(false);
  const [password, setPassword] = useState<string | null>(null);
  const changed = name.trim() !== person.name || !sameSet(picked, person.roles);

  const save = async (e: SyntheticEvent) => {
    e.preventDefault();
    if (!changed || !picked.length || !name.trim() || busy) return;
    setBusy(true);
    const p = await attempt(() => api.admin.updateUser(person.id, { name: name.trim(), roles: picked }), 'Changes not saved');
    setBusy(false);
    if (p) { onSaved(p); toast.success(`${p.name} updated`); onClose(); }
  };
  const flip = async () => {
    const status = person.status === 'active' ? 'disabled' : 'active';
    setBusy(true);
    const p = await attempt(() => api.admin.updateUser(person.id, { status }), status === 'disabled' ? 'Account not disabled' : 'Account not turned on');
    setBusy(false);
    if (!p) return;
    onSaved(p);
    toast.success(status === 'disabled' ? `${p.name} can no longer sign in` : `${p.name} can sign in again`,
      { description: status === 'disabled' ? 'Their open sessions ended at once.' : undefined });
    onClose();
  };
  const newPassword = async () => {
    const next = temporaryPassword();
    setBusy(true);
    const ok = await attempt(() => api.admin.resetPassword(person.id, next), 'Password not reset');
    setBusy(false);
    if (ok) setPassword(next);
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="sm:max-w-[520px]">
        <form onSubmit={save} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>{person.name}</DialogTitle>
            <DialogDescription>{person.email} · last signed in {when(person.lastLoginAt)}</DialogDescription>
          </DialogHeader>
          <Field label="Name" value={name} onChange={setName} />
          <div>
            <div className="mb-1.5 text-[12.5px] font-medium text-soft">Roles</div>
            <RolePicker roles={roles} value={picked} onChange={setPicked} isOwner={isOwner} />
          </div>
          {password ? <Credentials email={person.email} password={password} /> : (
            <div className="flex flex-wrap gap-2 border-t border-line/60 pt-3.5">
              <Button type="button" size="sm" variant="outline" onClick={newPassword} disabled={busy}>
                <KeyRound className="size-3.5" />Set a temporary password
              </Button>
              {!isMe && (
                <Button type="button" size="sm" variant={person.status === 'active' ? 'destructive' : 'outline'} onClick={flip} disabled={busy}>
                  {person.status === 'active'
                    ? <><UserRoundX className="size-3.5" />Disable account</>
                    : <><UserRoundCheck className="size-3.5" />Turn account back on</>}
                </Button>
              )}
            </div>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>{password ? 'Done' : 'Cancel'}</Button>
            <Button type="submit" disabled={!changed || !picked.length || !name.trim() || busy}>Save changes</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
