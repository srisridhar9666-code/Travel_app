import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CalendarRange,
  Copy,
  FileText,
  LockOpen,
  MailPlus,
  Pencil,
  Search,
  ShieldAlert,
  Upload,
  UserPlus,
  Users as UsersIcon,
} from 'lucide-react';
import { useState, type FormEvent } from 'react';
import toast from 'react-hot-toast';

import { BulkImportModal } from '@/components/BulkImportModal';
import { IdProofsPanel } from '@/components/IdProofsPanel';
import { TravelHistoryPanel } from '@/components/TravelHistoryPanel';
import { Modal } from '@/components/Modal';
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  Field,
  Input,
  Select,
  Skeleton,
} from '@/components/ui';
import {
  createUser,
  errorMessage,
  fetchRetentionStatus,
  fetchUsers,
  reinviteUser,
  runRetentionPurge,
  unlockUser,
  updateUser,
  type UserPayload,
} from '@/lib/api';
import { useAuth } from '@/store/auth';
import {
  DESIGNATION_LABELS,
  GENDER_LABELS,
  ROLE_LABELS,
  type Designation,
  type Gender,
  type Role,
  type UserRow,
} from '@/types';

const BLANK: UserPayload = {
  email: '',
  full_name: '',
  role: 'GROUND_STAFF',
  designation: 'EXECUTIVE',
  gender: 'UNDISCLOSED',
  phone: '',
  employee_code: '',
  base_location: '',
};

interface EditForm {
  full_name: string;
  role: string;
  designation: string;
  gender: string;
  phone: string;
  employee_code: string;
  base_location: string;
  exited_on: string;
}

function StatusBadge({ user }: { user: UserRow }) {
  if (user.exited_on) return <Badge tone="neutral">Left</Badge>;
  if (!user.is_active) return <Badge tone="neutral">Deactivated</Badge>;
  if (user.is_locked) return <Badge tone="danger">Locked</Badge>;
  if (!user.has_password) return <Badge tone="warning">Invited</Badge>;
  return <Badge tone="success">Active</Badge>;
}

/** Clipboard is unavailable on insecure origins, so always offer the raw link. */
async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success('Link copied');
  } catch {
    toast.error('Could not copy - select the link and copy it manually');
  }
}

export default function TeamPage() {
  const queryClient = useQueryClient();
  const me = useAuth((s) => s.user);
  const canGrantSystemAdmin = me?.role === 'SYSTEM_ADMIN';

  const [search, setSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState('');

  const [inviteOpen, setInviteOpen] = useState(false);
  const [form, setForm] = useState<UserPayload>(BLANK);
  const [formError, setFormError] = useState<string | null>(null);
  const [issuedLink, setIssuedLink] = useState<{ name: string; url: string } | null>(null);

  const [importOpen, setImportOpen] = useState(false);
  const [docsUser, setDocsUser] = useState<UserRow | null>(null);
  const [historyUser, setHistoryUser] = useState<UserRow | null>(null);
  const [editUser, setEditUser] = useState<UserRow | null>(null);
  const [editForm, setEditForm] = useState<EditForm | null>(null);
  const [editError, setEditError] = useState<string | null>(null);

  const users = useQuery({
    queryKey: ['users', search, roleFilter],
    queryFn: () =>
      fetchUsers({
        search: search.trim() || undefined,
        role: roleFilter || undefined,
        page_size: 100,
      }),
  });

  const retention = useQuery({ queryKey: ['retention'], queryFn: fetchRetentionStatus });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['users'] });
    queryClient.invalidateQueries({ queryKey: ['retention'] });
  };

  const invite = useMutation({
    mutationFn: () => createUser({ ...form, email: form.email.trim().toLowerCase() }),
    onSuccess: (result) => {
      setInviteOpen(false);
      setForm(BLANK);
      setFormError(null);
      refresh();
      if (result.invite_url) setIssuedLink({ name: form.full_name, url: result.invite_url });
    },
    onError: (err) => setFormError(errorMessage(err, 'Could not create this account.')),
  });

  const saveEdit = useMutation({
    mutationFn: () =>
      updateUser(editUser!.id, {
        full_name: editForm!.full_name,
        role: editForm!.role,
        designation: editForm!.designation || null,
        gender: editForm!.gender,
        phone: editForm!.phone || null,
        employee_code: editForm!.employee_code || null,
        base_location: editForm!.base_location || null,
        // Blank clears the exit date, which stops the retention clock.
        exited_on: editForm!.exited_on || null,
      } as never),
    onSuccess: (updated) => {
      toast.success(`${updated.full_name} updated`);
      setEditUser(null);
      setEditForm(null);
      setEditError(null);
      refresh();
    },
    onError: (err) => setEditError(errorMessage(err, 'Could not save these changes.')),
  });

  const reinvite = useMutation({
    mutationFn: (user: UserRow) => reinviteUser(user.id),
    onSuccess: (result, user) => {
      refresh();
      if (result.invite_url) setIssuedLink({ name: user.full_name, url: result.invite_url });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const unlock = useMutation({
    mutationFn: (user: UserRow) => unlockUser(user.id),
    onSuccess: (_r, user) => {
      toast.success(`${user.full_name} unlocked`);
      refresh();
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const toggleActive = useMutation({
    mutationFn: (user: UserRow) => updateUser(user.id, { is_active: !user.is_active }),
    onSuccess: (_r, user) => {
      toast.success(`${user.full_name} ${user.is_active ? 'deactivated' : 'reactivated'}`);
      refresh();
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const purge = useMutation({
    mutationFn: runRetentionPurge,
    onSuccess: (result) => {
      toast.success(`${result.purged} document(s) purged`);
      refresh();
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const openEdit = (user: UserRow) => {
    setEditUser(user);
    setEditForm({
      full_name: user.full_name,
      role: user.role,
      designation: user.designation ?? '',
      gender: user.gender,
      phone: user.phone ?? '',
      employee_code: user.employee_code ?? '',
      base_location: user.base_location ?? '',
      exited_on: user.exited_on ?? '',
    });
    setEditError(null);
  };

  const submitInvite = (event: FormEvent) => {
    event.preventDefault();
    setFormError(null);
    invite.mutate();
  };

  const submitEdit = (event: FormEvent) => {
    event.preventDefault();
    setEditError(null);
    saveEdit.mutate();
  };

  const rows = users.data?.items ?? [];
  const dueNow = retention.data?.due_now ?? 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Team</h1>
          <p className="mt-1.5 max-w-2xl text-sm text-text-muted">
            Accounts are created here and activated by the person through an invitation link.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => setImportOpen(true)}>
            <Upload size={15} />
            Import CSV
          </Button>
          <Button onClick={() => setInviteOpen(true)}>
            <UserPlus size={15} />
            Invite someone
          </Button>
        </div>
      </div>

      {/* Retention is surfaced before it deletes, not only in the ledger after. */}
      {dueNow > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-warning-soft px-4 py-3 text-sm text-warning">
          <ShieldAlert size={16} className="shrink-0" />
          <span className="flex-1">
            {dueNow} identity document{dueNow === 1 ? '' : 's'} belong to people who left more
            than {retention.data?.retention_days} days ago and are due for deletion.
          </span>
          {me?.role === 'SYSTEM_ADMIN' && (
            <Button
              size="sm"
              variant="secondary"
              loading={purge.isPending}
              onClick={() => {
                if (
                  window.confirm(
                    `Permanently purge ${dueNow} document(s)? Numbers and scans are deleted; the audit trail is kept.`,
                  )
                )
                  purge.mutate();
              }}
            >
              Purge now
            </Button>
          )}
        </div>
      )}

      <Card>
        <CardHeader
          title={`${users.data?.total ?? 0} ${users.data?.total === 1 ? 'person' : 'people'}`}
          action={
            <div className="flex gap-2">
              <div className="relative">
                <Search
                  size={14}
                  className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-subtle"
                />
                <Input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search name or email"
                  className="w-48 pl-8"
                  aria-label="Search team"
                />
              </div>
              <Select
                value={roleFilter}
                onChange={(e) => setRoleFilter(e.target.value)}
                aria-label="Filter by role"
                className="w-36"
              >
                <option value="">All roles</option>
                {(Object.keys(ROLE_LABELS) as Role[]).map((role) => (
                  <option key={role} value={role}>
                    {ROLE_LABELS[role]}
                  </option>
                ))}
              </Select>
            </div>
          }
        />

        {users.isPending ? (
          <div className="space-y-2 p-5">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : users.isError ? (
          <EmptyState
            icon={<UsersIcon size={28} />}
            title="Could not load the team"
            description={errorMessage(users.error)}
          />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<UsersIcon size={28} />}
            title="Nobody matches that"
            description="Try a different search, or invite someone new."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-2xs uppercase tracking-widest text-text-subtle">
                  <th className="px-5 py-2.5 font-semibold">Name</th>
                  <th className="px-5 py-2.5 font-semibold">Role</th>
                  <th className="hidden px-5 py-2.5 font-semibold md:table-cell">Designation</th>
                  <th className="hidden px-5 py-2.5 font-semibold lg:table-cell">Location</th>
                  <th className="px-5 py-2.5 font-semibold">Status</th>
                  <th className="px-5 py-2.5 text-right font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rows.map((user) => (
                  <tr key={user.id} className="transition-colors hover:bg-surface-sunken/60">
                    <td className="px-5 py-3">
                      <div className="font-medium">
                        {user.full_name}
                        {user.id === me?.id && (
                          <span className="ml-1.5 text-2xs text-text-subtle">(you)</span>
                        )}
                      </div>
                      <div className="text-xs text-text-muted">{user.email}</div>
                    </td>
                    <td className="px-5 py-3 text-text-muted">{ROLE_LABELS[user.role]}</td>
                    <td className="hidden px-5 py-3 text-text-muted md:table-cell">
                      {user.designation ? DESIGNATION_LABELS[user.designation] : '—'}
                    </td>
                    <td className="hidden px-5 py-3 text-text-muted lg:table-cell">
                      {user.base_location || '—'}
                    </td>
                    <td className="px-5 py-3">
                      <StatusBadge user={user} />
                    </td>
                    <td className="px-5 py-3">
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          title="Travel history"
                          onClick={() => setHistoryUser(user)}
                        >
                          <CalendarRange size={14} />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          title="Identity documents"
                          onClick={() => setDocsUser(user)}
                        >
                          <FileText size={14} />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          title="Edit profile"
                          onClick={() => openEdit(user)}
                        >
                          <Pencil size={14} />
                        </Button>
                        {user.is_locked && (
                          <Button
                            variant="ghost"
                            size="sm"
                            title="Unlock"
                            loading={unlock.isPending && unlock.variables?.id === user.id}
                            onClick={() => unlock.mutate(user)}
                          >
                            <LockOpen size={14} />
                          </Button>
                        )}
                        {user.is_active && (
                          <Button
                            variant="ghost"
                            size="sm"
                            title={user.has_password ? 'Send a reset link' : 'Resend invitation'}
                            loading={reinvite.isPending && reinvite.variables?.id === user.id}
                            onClick={() => reinvite.mutate(user)}
                          >
                            <MailPlus size={14} />
                          </Button>
                        )}
                        {user.id !== me?.id && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className={user.is_active ? 'text-danger hover:text-danger' : ''}
                            loading={
                              toggleActive.isPending && toggleActive.variables?.id === user.id
                            }
                            onClick={() => toggleActive.mutate(user)}
                          >
                            {user.is_active ? 'Deactivate' : 'Reactivate'}
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Travel history - SOW section 5 */}
      <Modal
        open={Boolean(historyUser)}
        onClose={() => setHistoryUser(null)}
        title={historyUser ? `${historyUser.full_name} — travel history` : ''}
        description="Every movement, including trips they were tagged onto by a colleague."
        className="sm:max-w-2xl"
        footer={
          <Button variant="secondary" onClick={() => setHistoryUser(null)}>
            Close
          </Button>
        }
      >
        {historyUser && <TravelHistoryPanel userId={historyUser.id} />}
      </Modal>

      {/* Identity documents */}
      <Modal
        open={Boolean(docsUser)}
        onClose={() => setDocsUser(null)}
        title={docsUser ? `${docsUser.full_name} — identity documents` : ''}
        description={docsUser?.email}
        className="sm:max-w-2xl"
        footer={
          <Button variant="secondary" onClick={() => setDocsUser(null)}>
            Close
          </Button>
        }
      >
        {docsUser && <IdProofsPanel user={docsUser} />}
      </Modal>

      {/* Edit profile */}
      <Modal
        open={Boolean(editUser)}
        onClose={() => {
          setEditUser(null);
          setEditError(null);
        }}
        title={editUser ? `Edit ${editUser.full_name}` : ''}
        description="Every change is recorded in the activity log."
        footer={
          <>
            <Button variant="secondary" onClick={() => setEditUser(null)}>
              Cancel
            </Button>
            <Button form="edit-form" type="submit" loading={saveEdit.isPending}>
              Save changes
            </Button>
          </>
        }
      >
        {editForm && (
          <form id="edit-form" onSubmit={submitEdit} className="space-y-4" noValidate>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Full name" htmlFor="edit_name" required className="sm:col-span-2">
                <Input
                  id="edit_name"
                  required
                  value={editForm.full_name}
                  onChange={(e) => setEditForm({ ...editForm, full_name: e.target.value })}
                />
              </Field>

              <Field label="Role" htmlFor="edit_role">
                <Select
                  id="edit_role"
                  value={editForm.role}
                  disabled={editUser?.id === me?.id}
                  onChange={(e) => setEditForm({ ...editForm, role: e.target.value })}
                >
                  <option value="GROUND_STAFF">{ROLE_LABELS.GROUND_STAFF}</option>
                  <option value="ADMIN">{ROLE_LABELS.ADMIN}</option>
                  {canGrantSystemAdmin && (
                    <option value="SYSTEM_ADMIN">{ROLE_LABELS.SYSTEM_ADMIN}</option>
                  )}
                </Select>
              </Field>

              <Field label="Designation" htmlFor="edit_designation">
                <Select
                  id="edit_designation"
                  value={editForm.designation}
                  onChange={(e) => setEditForm({ ...editForm, designation: e.target.value })}
                >
                  <option value="">Not set</option>
                  {(Object.keys(DESIGNATION_LABELS) as Designation[]).map((d) => (
                    <option key={d} value={d}>
                      {DESIGNATION_LABELS[d]}
                    </option>
                  ))}
                </Select>
              </Field>

              <Field label="Gender" htmlFor="edit_gender" hint="Room-sharing policy only.">
                <Select
                  id="edit_gender"
                  value={editForm.gender}
                  onChange={(e) => setEditForm({ ...editForm, gender: e.target.value })}
                >
                  {(Object.keys(GENDER_LABELS) as Gender[]).map((g) => (
                    <option key={g} value={g}>
                      {GENDER_LABELS[g]}
                    </option>
                  ))}
                </Select>
              </Field>

              <Field label="Base location" htmlFor="edit_location">
                <Input
                  id="edit_location"
                  value={editForm.base_location}
                  onChange={(e) => setEditForm({ ...editForm, base_location: e.target.value })}
                />
              </Field>

              <Field label="Phone" htmlFor="edit_phone">
                <Input
                  id="edit_phone"
                  value={editForm.phone}
                  onChange={(e) => setEditForm({ ...editForm, phone: e.target.value })}
                />
              </Field>

              <Field label="Employee code" htmlFor="edit_code">
                <Input
                  id="edit_code"
                  value={editForm.employee_code}
                  onChange={(e) => setEditForm({ ...editForm, employee_code: e.target.value })}
                />
              </Field>

              <Field
                label="Exit date"
                htmlFor="edit_exit"
                className="sm:col-span-2"
                hint="Starts the 90-day clock on their identity documents. Leave blank for current staff — deactivating alone does not count as leaving."
              >
                <Input
                  id="edit_exit"
                  type="date"
                  value={editForm.exited_on}
                  onChange={(e) => setEditForm({ ...editForm, exited_on: e.target.value })}
                />
              </Field>
            </div>

            {editError && (
              <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-xs text-danger">
                {editError}
              </p>
            )}
          </form>
        )}
      </Modal>

      {/* Invite */}
      <Modal
        open={inviteOpen}
        onClose={() => {
          setInviteOpen(false);
          setFormError(null);
        }}
        title="Invite someone"
        description="They set their own password from the link — you never see it."
        footer={
          <>
            <Button variant="secondary" onClick={() => setInviteOpen(false)}>
              Cancel
            </Button>
            <Button form="invite-form" type="submit" loading={invite.isPending}>
              Send invitation
            </Button>
          </>
        }
      >
        <form id="invite-form" onSubmit={submitInvite} className="space-y-4" noValidate>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Full name" htmlFor="full_name" required className="sm:col-span-2">
              <Input
                id="full_name"
                required
                value={form.full_name}
                onChange={(e) => setForm({ ...form, full_name: e.target.value })}
                placeholder="Ravi Kumar"
              />
            </Field>

            <Field label="Work email" htmlFor="new_email" required className="sm:col-span-2">
              <Input
                id="new_email"
                type="email"
                required
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
                placeholder="ravi@designboxed.com"
              />
            </Field>

            <Field label="Role" htmlFor="role" required>
              <Select
                id="role"
                value={form.role}
                onChange={(e) => setForm({ ...form, role: e.target.value })}
              >
                <option value="GROUND_STAFF">{ROLE_LABELS.GROUND_STAFF}</option>
                <option value="ADMIN">{ROLE_LABELS.ADMIN}</option>
                {canGrantSystemAdmin && (
                  <option value="SYSTEM_ADMIN">{ROLE_LABELS.SYSTEM_ADMIN}</option>
                )}
              </Select>
            </Field>

            <Field
              label="Designation"
              htmlFor="designation"
              hint="Hierarchy only — it does not affect approvals."
            >
              <Select
                id="designation"
                value={form.designation ?? ''}
                onChange={(e) => setForm({ ...form, designation: e.target.value || null })}
              >
                <option value="">Not set</option>
                {(Object.keys(DESIGNATION_LABELS) as Designation[]).map((d) => (
                  <option key={d} value={d}>
                    {DESIGNATION_LABELS[d]}
                  </option>
                ))}
              </Select>
            </Field>

            <Field label="Gender" htmlFor="gender" hint="Used only for the room-sharing policy.">
              <Select
                id="gender"
                value={form.gender}
                onChange={(e) => setForm({ ...form, gender: e.target.value })}
              >
                {(Object.keys(GENDER_LABELS) as Gender[]).map((g) => (
                  <option key={g} value={g}>
                    {GENDER_LABELS[g]}
                  </option>
                ))}
              </Select>
            </Field>

            <Field label="Base location" htmlFor="base_location">
              <Input
                id="base_location"
                value={form.base_location ?? ''}
                onChange={(e) => setForm({ ...form, base_location: e.target.value })}
                placeholder="Hyderabad"
              />
            </Field>

            <Field label="Phone" htmlFor="phone">
              <Input
                id="phone"
                value={form.phone ?? ''}
                onChange={(e) => setForm({ ...form, phone: e.target.value })}
                placeholder="+91 98765 43210"
              />
            </Field>

            <Field label="Employee code" htmlFor="employee_code">
              <Input
                id="employee_code"
                value={form.employee_code ?? ''}
                onChange={(e) => setForm({ ...form, employee_code: e.target.value })}
                placeholder="DB-1042"
              />
            </Field>
          </div>

          {formError && (
            <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-xs text-danger">
              {formError}
            </p>
          )}
        </form>
      </Modal>

      {/* The invite link, shown once. Email delivery lands in Phase 6. */}
      <Modal
        open={Boolean(issuedLink)}
        onClose={() => setIssuedLink(null)}
        title="Invitation link"
        description="Email delivery arrives in Phase 6 — until then, send this to them yourself."
        footer={
          <>
            <Button variant="secondary" onClick={() => setIssuedLink(null)}>
              Done
            </Button>
            <Button onClick={() => issuedLink && copy(issuedLink.url)}>
              <Copy size={14} />
              Copy link
            </Button>
          </>
        }
      >
        <p className="text-sm text-text-muted">
          Send this to <span className="font-medium text-text">{issuedLink?.name}</span>. It can be
          used once and expires in 72 hours.
        </p>
        <code className="mt-3 block max-h-32 overflow-auto break-all rounded-md bg-surface-sunken px-3 py-2.5 font-mono text-xs text-text-muted">
          {issuedLink?.url}
        </code>
      </Modal>

      <BulkImportModal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onImported={refresh}
      />
    </div>
  );
}
