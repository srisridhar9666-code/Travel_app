import { CheckCircle2 } from 'lucide-react';

/** What a new password still needs, under the field. Nothing while it is empty. */
export function PasswordChecklist({ password, issues }: { password: string; issues: string[] }) {
  if (password.length === 0) return null;
  return (
    <ul className="space-y-1 rounded-md bg-surface-sunken px-3 py-2.5">
      {issues.length === 0 ? (
        <li className="flex items-center gap-2 text-xs text-success">
          <CheckCircle2 size={13} />
          Looks good
        </li>
      ) : (
        issues.map((issue) => (
          <li key={issue} className="flex items-center gap-2 text-xs text-text-muted">
            <span className="h-1 w-1 rounded-full bg-text-subtle" />
            {issue}
          </li>
        ))
      )}
    </ul>
  );
}
