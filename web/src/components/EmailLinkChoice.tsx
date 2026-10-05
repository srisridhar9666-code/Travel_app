/** "Email the link to them" - the choice on every invite, reset, and approved
 *  team addition. The link is shown afterwards either way, to copy. */
export function EmailLinkChoice({
  name,
  checked,
  onChange,
}: {
  name: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2.5 rounded-md bg-surface-sunken px-3 py-2.5">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 h-3.5 w-3.5 accent-[rgb(var(--primary))]"
      />
      <span className="text-xs">
        <span className="font-medium">Email the link to {name || 'them'}</span>
        <span className="block text-text-muted">
          {checked
            ? 'They get it by email. You also see it next, to copy and share.'
            : 'No email. You copy the link next and share it yourself - on WhatsApp, SMS or email.'}
        </span>
      </span>
    </label>
  );
}
