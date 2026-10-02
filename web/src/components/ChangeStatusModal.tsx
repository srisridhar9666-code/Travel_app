import { useMutation, useQuery } from '@tanstack/react-query';
import { AlertTriangle } from 'lucide-react';
import { useState } from 'react';
import toast from 'react-hot-toast';

import { Modal } from '@/components/Modal';
import { Button, Field, Input } from '@/components/ui';
import { changeUserStatus, errorMessage, fetchUserOpenTrips } from '@/lib/api';
import { todayInIndia } from '@/lib/time';
import { cn } from '@/lib/utils';
import { USER_STATUS_HELP, USER_STATUS_LABELS, type UserRow, type UserStatus } from '@/types';

const ORDER: UserStatus[] = ['ACTIVE', 'DEACTIVATED', 'LEFT', 'DELETED'];

/** How the toast finishes "Ravi Kumar is now ...". */
const NOW: Record<UserStatus, string> = {
  ACTIVE: 'active',
  DEACTIVATED: 'deactivated',
  LEFT: 'marked as left',
  DELETED: 'deleted',
};

function confirmLabel(choice: UserStatus, user: UserRow): string {
  const first = user.full_name.split(' ')[0];
  switch (choice) {
    case 'ACTIVE':
      return user.status === 'DELETED' ? `Restore ${first}` : `Reactivate ${first}`;
    case 'DEACTIVATED':
      return `Deactivate ${first}`;
    case 'LEFT':
      return 'Mark as left';
    case 'DELETED':
      return `Delete ${user.full_name}`;
  }
}

interface ChangeStatusModalProps {
  /** Give the modal `key={user?.id}` so each person starts from a clean form. */
  user: UserRow | null;
  onClose: () => void;
  onChanged: () => void;
}

/**
 * Active, Deactivated, Left or Deleted - which decides whether someone can
 * sign in. It is also the "are you sure?" step: the button names what will
 * happen, and the admin is told about trips still on the books first.
 */
export function ChangeStatusModal({ user, onClose, onChanged }: ChangeStatusModalProps) {
  const today = todayInIndia();
  const [choice, setChoice] = useState<UserStatus>(user?.status ?? 'ACTIVE');
  const [exitedOn, setExitedOn] = useState(user?.exited_on ?? today);
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);

  const switchingOff = choice !== 'ACTIVE';
  const trips = useQuery({
    queryKey: ['open-trips', user?.id],
    queryFn: () => fetchUserOpenTrips(user!.id),
    enabled: Boolean(user) && switchingOff,
  });

  const unchanged =
    !user ||
    (choice === user.status && (choice !== 'LEFT' || exitedOn === (user.exited_on ?? today)));

  const save = useMutation({
    mutationFn: () =>
      changeUserStatus(user!.id, {
        status: choice,
        exited_on: choice === 'LEFT' ? exitedOn || null : null,
        reason: reason.trim() || null,
      }),
    meta: { errorFallback: 'Could not change the status.' },
    onSuccess: (updated) => {
      toast.success(`${updated.full_name} is now ${NOW[updated.status]}`);
      onChanged();
      onClose();
    },
    onError: (err) => setError(errorMessage(err, 'Could not change the status.')),
  });

  const open = trips.data?.total ?? 0;

  return (
    <Modal
      open={Boolean(user)}
      onClose={onClose}
      title={user ? `Change ${user.full_name}'s status` : ''}
      description="Only Active can sign in. The change applies straight away, even if they are signed in now."
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={save.isPending}>
            Cancel
          </Button>
          {user && (
            <Button
              variant={choice === 'ACTIVE' ? 'primary' : 'danger'}
              disabled={unchanged}
              loading={save.isPending}
              onClick={() => {
                setError(null);
                save.mutate();
              }}
            >
              {confirmLabel(choice, user)}
            </Button>
          )}
        </>
      }
    >
      {user && (
        <div className="space-y-4">
          <fieldset className="space-y-2">
            <legend className="sr-only">Status</legend>
            {ORDER.map((status) => (
              <label
                key={status}
                className={cn(
                  'flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 transition-colors',
                  choice === status
                    ? status === 'DELETED'
                      ? 'border-danger bg-danger-soft/40'
                      : 'border-border-strong bg-surface-sunken'
                    : 'border-border hover:bg-surface-sunken/60',
                )}
              >
                <input
                  type="radio"
                  name="user-status"
                  value={status}
                  checked={choice === status}
                  onChange={() => {
                    setChoice(status);
                    setError(null);
                  }}
                  className="mt-1"
                />
                <span className="min-w-0">
                  <span
                    className={cn(
                      'text-sm font-medium',
                      status === 'DELETED' && choice === status && 'text-danger',
                    )}
                  >
                    {USER_STATUS_LABELS[status]}
                    {status === user.status && (
                      <span className="ml-1.5 text-2xs font-normal text-text-subtle">(now)</span>
                    )}
                  </span>
                  <span className="block text-xs text-text-muted">{USER_STATUS_HELP[status]}</span>
                </span>
              </label>
            ))}
          </fieldset>

          {choice === 'LEFT' && (
            <Field
              label="Exit date"
              htmlFor="status_exit"
              required
              hint="Their last day. Their ID documents are deleted 90 days after it."
            >
              <Input
                id="status_exit"
                type="date"
                required
                max={today}
                value={exitedOn}
                onChange={(e) => setExitedOn(e.target.value)}
              />
            </Field>
          )}

          {!unchanged && (
            <Field label="Reason (kept in the activity log)" htmlFor="status_reason">
              <textarea
                id="status_reason"
                rows={2}
                maxLength={500}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder={choice === 'ACTIVE' ? 'Back from leave' : 'Resigned, last day 30 Sept'}
                className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text transition-colors placeholder:text-text-subtle hover:border-border-strong"
              />
            </Field>
          )}

          {switchingOff && open > 0 && trips.data && (
            <div className="flex items-start gap-2 rounded-md bg-warning-soft px-3 py-2.5 text-xs text-warning">
              <AlertTriangle size={14} className="mt-px shrink-0" />
              <span>
                {open} {open === 1 ? 'trip is' : 'trips are'} still on the books (
                {trips.data.pending} pending, {trips.data.approved} approved,{' '}
                {trips.data.booked} booked). They are not cancelled automatically. Cancel them in
                Approvals if the trip is off.
              </span>
            </div>
          )}

          {error && (
            <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-xs text-danger">
              {error}
            </p>
          )}
        </div>
      )}
    </Modal>
  );
}
