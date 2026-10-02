import { useMutation, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, KeyRound, Mail, UserRound } from 'lucide-react';
import { useMemo, useState, type FormEvent, type ReactNode } from 'react';
import toast from 'react-hot-toast';

import { PasswordChecklist } from '@/components/PasswordChecklist';
import { Button, Card, CardHeader, Field, Input, PageHeader } from '@/components/ui';
import { changeMyEmail, changePassword, errorMessage, updateMyProfile } from '@/lib/api';
import { passwordIssues } from '@/lib/password';
import { formatInstant } from '@/lib/time';
import { useAuth } from '@/store/auth';
import {
  DESIGNATION_LABELS,
  GENDER_LABELS,
  ROLE_LABELS,
  type ProfileUpdate,
  type UserProfile,
} from '@/types';

function ErrorBox({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-md bg-danger-soft px-3 py-2.5 text-xs text-danger"
    >
      <AlertCircle size={14} className="mt-px shrink-0" />
      <span>{message}</span>
    </div>
  );
}

function ReadOnly({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-2xs font-semibold uppercase tracking-wider text-text-subtle">{label}</dt>
      <dd className="mt-0.5 text-sm">{children || '—'}</dd>
    </div>
  );
}

/**
 * Everyone's own page: name and phone, the email they sign in with, and their
 * password. The rest of their record - role, department, gender, base - is set
 * by an admin and shown here read-only, so they can see what is on file.
 */
export default function ProfilePage() {
  const user = useAuth((s) => s.user);
  if (!user) return null;
  return <Profile user={user} />;
}

function Profile({ user }: { user: UserProfile }) {
  const queryClient = useQueryClient();

  /** Keep the shell, and the cached copy the shell refreshes from, in step. */
  const adopt = (updated: UserProfile) => {
    useAuth.getState().setUser(updated);
    queryClient.setQueryData(['me'], updated);
  };

  // --- personal details ----------------------------------------------------
  // Null until they type, so an admin's edit that arrives on the next refresh
  // shows up instead of being hidden behind a stale copy.
  const [draft, setDraft] = useState<{ full_name: string; phone: string } | null>(null);
  const details = draft ?? { full_name: user.full_name, phone: user.phone ?? '' };
  const [detailsError, setDetailsError] = useState<string | null>(null);

  const changes = useMemo(() => {
    const out: ProfileUpdate = {};
    const name = details.full_name.trim();
    if (name !== user.full_name) out.full_name = name;
    const phone = details.phone.trim();
    if (phone !== (user.phone ?? '')) out.phone = phone || null;
    return out;
  }, [details, user]);
  const dirty = Object.keys(changes).length > 0;

  const saveDetails = useMutation({
    mutationFn: () => updateMyProfile(changes),
    meta: { errorFallback: 'Could not save your details.' },
    onSuccess: (updated) => {
      adopt(updated);
      setDraft(null);
      setDetailsError(null);
      toast.success('Profile saved');
    },
    onError: (err) => setDetailsError(errorMessage(err, 'Could not save your details.')),
  });

  // --- sign-in email ---------------------------------------------------------
  const [newEmail, setNewEmail] = useState('');
  const [emailPassword, setEmailPassword] = useState('');
  const [emailError, setEmailError] = useState<string | null>(null);

  const saveEmail = useMutation({
    mutationFn: () => changeMyEmail(newEmail.trim(), emailPassword),
    meta: { errorFallback: 'Could not change your email.' },
    onSuccess: (updated) => {
      adopt(updated);
      setNewEmail('');
      setEmailPassword('');
      setEmailError(null);
      toast.success(`You now sign in as ${updated.email}`);
    },
    onError: (err) => setEmailError(errorMessage(err, 'Could not change your email.')),
  });

  // --- password --------------------------------------------------------------
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [passwordError, setPasswordError] = useState<string | null>(null);

  const issues = useMemo(
    () => passwordIssues(next, user.email, user.full_name),
    [next, user.email, user.full_name],
  );
  const mismatch = confirm.length > 0 && next !== confirm;
  const passwordReady =
    current.length > 0 && next.length > 0 && issues.length === 0 && confirm.length > 0 && !mismatch;

  const savePassword = useMutation({
    mutationFn: () => changePassword(current, next),
    meta: { errorFallback: 'Could not change your password.' },
    onSuccess: (result) => {
      // Every token issued before now has just stopped working, this one
      // included; carry on with the fresh one.
      useAuth.getState().replaceToken(result.access_token, result.expires_at);
      queryClient.invalidateQueries({ queryKey: ['me'] });
      setCurrent('');
      setNext('');
      setConfirm('');
      setPasswordError(null);
      toast.success('Password changed. Your other devices are signed out.');
    },
    onError: (err) => setPasswordError(errorMessage(err, 'Could not change your password.')),
  });

  const submit = (run: () => void, clear: () => void) => (event: FormEvent) => {
    event.preventDefault();
    clear();
    run();
  };

  const base = [user.base_location, user.base_state].filter(Boolean).join(', ');

  return (
    <div className="space-y-6">
      <PageHeader
        title="My profile"
        description="Your name, phone and sign-in details. Every change is recorded in the activity log."
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Personal details" />
          <form
            onSubmit={submit(() => saveDetails.mutate(), () => setDetailsError(null))}
            className="space-y-4 p-4 sm:p-5"
            noValidate
          >
            <Field label="Full name" htmlFor="profile_name" required>
              <Input
                id="profile_name"
                autoComplete="name"
                required
                value={details.full_name}
                onChange={(e) => setDraft({ ...details, full_name: e.target.value })}
              />
            </Field>
            <Field label="Phone" htmlFor="profile_phone" hint="10 to 15 digits.">
              <Input
                id="profile_phone"
                type="tel"
                autoComplete="tel"
                value={details.phone}
                onChange={(e) => setDraft({ ...details, phone: e.target.value })}
                placeholder="+91 98765 43210"
              />
            </Field>

            <div className="rounded-md bg-surface-sunken px-3 py-3">
              <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
                <ReadOnly label="App access">{ROLE_LABELS[user.role]}</ReadOnly>
                <ReadOnly label="Designation">
                  {user.designation ? DESIGNATION_LABELS[user.designation] : null}
                </ReadOnly>
                <ReadOnly label="Employee code">{user.employee_code}</ReadOnly>
                <ReadOnly label="Gender">{GENDER_LABELS[user.gender]}</ReadOnly>
                <ReadOnly label="Department">{user.department_name}</ReadOnly>
                <ReadOnly label="Base">{base}</ReadOnly>
              </dl>
              <p className="mt-3 text-xs text-text-subtle">Managed by your administrator.</p>
            </div>

            <ErrorBox message={detailsError} />

            <div className="flex justify-end gap-2">
              {dirty && (
                <Button type="button" variant="secondary" onClick={() => setDraft(null)}>
                  Undo
                </Button>
              )}
              <Button type="submit" disabled={!dirty} loading={saveDetails.isPending}>
                <UserRound size={15} />
                Save changes
              </Button>
            </div>
          </form>
        </Card>

        <Card>
          <CardHeader
            title="Sign-in email"
            description="You sign in with this address and password-reset links go to it."
          />
          <form
            onSubmit={submit(() => saveEmail.mutate(), () => setEmailError(null))}
            className="space-y-4 p-4 sm:p-5"
            noValidate
          >
            <ReadOnly label="Current email">{user.email}</ReadOnly>
            <Field label="New email" htmlFor="profile_email" required>
              <Input
                id="profile_email"
                type="email"
                autoComplete="email"
                required
                value={newEmail}
                onChange={(e) => setNewEmail(e.target.value)}
                placeholder="you@designboxed.com"
              />
            </Field>
            <Field
              label="Current password"
              htmlFor="profile_email_password"
              required
              hint="Needed so nobody else can move your account to their address."
            >
              <Input
                id="profile_email_password"
                type="password"
                autoComplete="current-password"
                required
                value={emailPassword}
                onChange={(e) => setEmailPassword(e.target.value)}
              />
            </Field>

            <ErrorBox message={emailError} />

            <div className="flex justify-end">
              <Button
                type="submit"
                disabled={!newEmail.trim() || !emailPassword}
                loading={saveEmail.isPending}
              >
                <Mail size={15} />
                Change email
              </Button>
            </div>
          </form>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader
            title="Password"
            description={
              user.password_changed_at
                ? `Last changed ${formatInstant(user.password_changed_at)}. Changing it signs you out on your other devices.`
                : 'Changing it signs you out on your other devices.'
            }
          />
          <form
            onSubmit={submit(() => savePassword.mutate(), () => setPasswordError(null))}
            className="grid gap-4 p-4 sm:grid-cols-3 sm:p-5"
            noValidate
          >
            <Field label="Current password" htmlFor="profile_current" required>
              <Input
                id="profile_current"
                type="password"
                autoComplete="current-password"
                required
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
              />
            </Field>
            <Field label="New password" htmlFor="profile_new" required>
              <Input
                id="profile_new"
                type="password"
                autoComplete="new-password"
                required
                value={next}
                onChange={(e) => setNext(e.target.value)}
              />
            </Field>
            <Field
              label="Confirm new password"
              htmlFor="profile_confirm"
              required
              error={mismatch ? 'Passwords do not match.' : null}
            >
              <Input
                id="profile_confirm"
                type="password"
                autoComplete="new-password"
                required
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                aria-invalid={mismatch}
              />
            </Field>

            <div className="space-y-4 sm:col-span-3">
              <PasswordChecklist password={next} issues={issues} />
              <ErrorBox message={passwordError} />
              <div className="flex justify-end">
                <Button type="submit" disabled={!passwordReady} loading={savePassword.isPending}>
                  <KeyRound size={15} />
                  Change password
                </Button>
              </div>
            </div>
          </form>
        </Card>
      </div>
    </div>
  );
}
