import { useMutation, useQuery } from '@tanstack/react-query';
import { AlertCircle, CheckCircle2, ShieldCheck } from 'lucide-react';
import { useMemo, useState, type FormEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import toast from 'react-hot-toast';

import { LogoLockup } from '@/components/Logo';
import { ThemeToggle } from '@/components/ThemeToggle';
import { Button, Field, Input, Spinner } from '@/components/ui';
import { errorMessage, previewToken, setPassword } from '@/lib/api';
import { useAuth } from '@/store/auth';

const MIN_LENGTH = 10;

/** Mirrors the server-side policy so the user is told before they submit.
 *  The server remains the authority - this is courtesy, not enforcement. */
function localIssues(password: string, email?: string, name?: string): string[] {
  const issues: string[] = [];
  if (password.length < MIN_LENGTH) issues.push(`At least ${MIN_LENGTH} characters`);
  if (new Set(password).size < 5) issues.push('A greater variety of characters');
  const lowered = password.toLowerCase();
  if (email) {
    const local = email.split('@')[0].toLowerCase();
    if (local.length >= 4 && lowered.includes(local)) issues.push('Must not contain your email');
  }
  if (name) {
    for (const part of name.toLowerCase().split(/\s+/)) {
      if (part.length >= 4 && lowered.includes(part)) {
        issues.push('Must not contain your name');
        break;
      }
    }
  }
  return issues;
}

export default function SetPasswordPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get('token') ?? '';

  const [password, setPasswordValue] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);

  const preview = useQuery({
    queryKey: ['token', token],
    queryFn: () => previewToken(token),
    enabled: Boolean(token),
    retry: false,
  });

  const issues = useMemo(
    () => localIssues(password, preview.data?.email, preview.data?.full_name),
    [password, preview.data],
  );
  const mismatch = confirm.length > 0 && password !== confirm;
  const ready = password.length > 0 && issues.length === 0 && !mismatch && confirm.length > 0;

  const mutation = useMutation({
    mutationFn: () => setPassword(token, password),
    onSuccess: () => {
      // Whoever redeemed this link is almost certainly not whoever was last
      // signed in on this browser. Without clearing, accepting an invite on a
      // colleague's machine drops you straight into their session.
      useAuth.getState().signOut();
      toast.success('Password set. Sign in to continue.');
      navigate('/login', { replace: true });
    },
    onError: (err) => setError(errorMessage(err, 'Could not set your password.')),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    mutation.mutate();
  };

  const isInvite = preview.data?.purpose === 'INVITE';

  return (
    <div className="flex min-h-dvh flex-col bg-canvas px-6 py-8">
      <div className="mx-auto flex w-full max-w-sm items-center justify-between">
        <LogoLockup />
        <ThemeToggle />
      </div>

      <div className="flex flex-1 items-center justify-center">
        <div className="w-full max-w-sm animate-slide-up">
          {!token || preview.isError ? (
            <div className="text-center">
              <AlertCircle size={28} className="mx-auto text-danger" />
              <h1 className="mt-4 text-xl font-semibold tracking-tight">This link is not valid</h1>
              <p className="mt-2 text-sm leading-relaxed text-text-muted">
                Invitation and reset links expire. Ask an administrator to send you a fresh one.
              </p>
              <Link to="/login" className="mt-6 inline-block">
                <Button variant="secondary">Back to sign in</Button>
              </Link>
            </div>
          ) : preview.isPending ? (
            <div className="flex justify-center py-12">
              <Spinner className="h-5 w-5" />
            </div>
          ) : (
            <>
              <ShieldCheck size={24} className="text-brand" strokeWidth={2} />
              <h1 className="mt-4 text-2xl font-semibold tracking-tight">
                {isInvite ? 'Set your password' : 'Choose a new password'}
              </h1>
              <p className="mt-2 text-sm leading-relaxed text-text-muted">
                {isInvite ? 'Welcome, ' : 'Signed in as '}
                <span className="font-medium text-text">{preview.data.full_name}</span> &mdash;{' '}
                {preview.data.email}
              </p>

              <form onSubmit={submit} className="mt-8 space-y-4" noValidate>
                <Field label="New password" htmlFor="password" required>
                  <Input
                    id="password"
                    type="password"
                    autoComplete="new-password"
                    autoFocus
                    required
                    value={password}
                    onChange={(e) => setPasswordValue(e.target.value)}
                    placeholder="••••••••••"
                  />
                </Field>

                <Field
                  label="Confirm password"
                  htmlFor="confirm"
                  required
                  error={mismatch ? 'Passwords do not match.' : null}
                >
                  <Input
                    id="confirm"
                    type="password"
                    autoComplete="new-password"
                    required
                    value={confirm}
                    onChange={(e) => setConfirm(e.target.value)}
                    placeholder="••••••••••"
                    aria-invalid={mismatch}
                  />
                </Field>

                {password.length > 0 && (
                  <ul className="space-y-1 rounded-md bg-surface-sunken px-3 py-2.5">
                    {issues.length === 0 ? (
                      <li className="flex items-center gap-2 text-xs text-success">
                        <CheckCircle2 size={13} />
                        Looks good
                      </li>
                    ) : (
                      issues.map((issue) => (
                        <li
                          key={issue}
                          className="flex items-center gap-2 text-xs text-text-muted"
                        >
                          <span className="h-1 w-1 rounded-full bg-text-subtle" />
                          {issue}
                        </li>
                      ))
                    )}
                  </ul>
                )}

                {error && (
                  <div
                    role="alert"
                    className="flex items-start gap-2 rounded-md bg-danger-soft px-3 py-2.5 text-xs text-danger"
                  >
                    <AlertCircle size={14} className="mt-px shrink-0" />
                    <span>{error}</span>
                  </div>
                )}

                <Button
                  type="submit"
                  size="lg"
                  className="w-full"
                  disabled={!ready}
                  loading={mutation.isPending}
                >
                  {isInvite ? 'Set password and continue' : 'Update password'}
                </Button>
              </form>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
